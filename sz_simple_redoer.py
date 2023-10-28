#! /usr/bin/env python3

import concurrent.futures

import argparse
import orjson
import logging
import traceback

import importlib
import sys
import os
import time
import random

from senzing import (
    G2Engine,
    G2Exception,
    ExceptionCode,
    G2EngineFlags,
    G2BadInputException,
    G2RetryTimeoutExceeded,
)

INTERVAL = 1000
LONG_RECORD = os.getenv("LONG_RECORD", default=300)
EMPTY_PAUSE_TIME = int(os.getenv("SENZING_REDO_SLEEP_TIME_IN_SECONDS", default=60))

TUPLE_MSG = 0
TUPLE_STARTTIME = 1

log_format = "%(asctime)s %(message)s"

def loggingID(rec):
    dsrc = rec.get("DATA_SOURCE")
    rec_id = rec.get("RECORD_ID")
    if dsrc and rec_id:
        return f'{dsrc} : {rec_id}'
    umf_proc = rec.get("UMF_PROC") # repair messages
    if umf_proc:
        return f'{umf_proc[PARAMS][0][PARAM][VALUE]} : REPAIR_ENTITY'
    return "UNKNOWN RECORD"

def process_msg(engine, msg, info):
    try:
        if info:
            response = bytearray()
            engine.processWithInfo(msg, response)
            return response.decode()
        else:
            engine.process(msg)
            return None
    except Exception as err:
        print(f"{err} [{msg}]", file=sys.stderr)
        raise


try:
    log_level_map = {
        "notset": logging.NOTSET,
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "fatal": logging.FATAL,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }

    log_level_parameter = os.getenv("SENZING_LOG_LEVEL", "info").lower()
    log_level = log_level_map.get(log_level_parameter, logging.INFO)
    logging.basicConfig(format=log_format, level=log_level)

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-i",
        "--info",
        dest="info",
        action="store_true",
        default=False,
        help="produce withinfo messages",
    )
    parser.add_argument(
        "-t",
        "--debugTrace",
        dest="debugTrace",
        action="store_true",
        default=False,
        help="output debug trace information",
    )
    args = parser.parse_args()

    engine_config = os.getenv("SENZING_ENGINE_CONFIGURATION_JSON")
    if not engine_config:
        print(
            "The environment variable SENZING_ENGINE_CONFIGURATION_JSON must be set with a proper JSON configuration.",
            file=sys.stderr,
        )
        print(
            "Please see https://senzing.zendesk.com/hc/en-us/articles/360038774134-G2Module-Configuration-and-the-Senzing-API",
            file=sys.stderr,
        )
        exit(-1)

    # Initialize the G2Engine
    g2 = G2Engine()
    g2.init("sz_simple_redoer", engine_config, args.debugTrace)
    logCheckTime = prevTime = time.time()

    senzing_governor = importlib.import_module("senzing_governor")
    governor = senzing_governor.Governor(hint="sz_simple_redoer")

    max_workers = int(os.getenv("SENZING_THREADS_PER_PROCESS", 0))

    if not max_workers:  # reset to null for executors
        max_workers = None

    messages = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers) as executor:
        print(f"Threads: {executor._max_workers}")
        futures = {}
        empty_pause = 0
        try:
            while True:

                nowTime = time.time()
                if futures:
                    done, _ = concurrent.futures.wait(
                        futures,
                        timeout=10,
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )

                    delete_batch = []
                    delete_cnt = 0

                    for fut in done:
                        msg = futures.pop(fut)
                        try:
                            result = fut.result()
                            if result:
                                print(
                                    result
                                )  # we would handle pushing to withinfo queues here BUT that is likely a second future task/executor
                        except G2BadInputException as err:
                            if (
                                ExceptionCode(err) == 7426
                            ):  # log transliteration issue specially
                                print(f"Transliteration failure: {msg[TUPLE_MSG]}")
                            pass
                        except G2RetryTimeoutExceeded as err:
                            print("Hit retry timeout")

                        messages += 1

                        if messages % INTERVAL == 0:  # display rate stats
                            diff = nowTime - prevTime
                            speed = -1
                            if diff > 0.0:
                                speed = int(INTERVAL / diff)
                            print(
                                f"Processed {messages} redo, {speed} records per second"
                            )
                            prevTime = nowTime

                    if nowTime > logCheckTime + (
                        LONG_RECORD / 2
                    ):  # log long running records
                        logCheckTime = nowTime

                        response = bytearray()
                        g2.stats(response)
                        print(f"\n{response.decode()}\n")

                        numStuck = 0
                        numRejected = 0
                        for fut, msg in futures.items():
                            if not fut.done():
                                duration = nowTime - msg[TUPLE_STARTTIME]
                                if duration > LONG_RECORD * 2:
                                    numStuck += 1
                                    record = orjson.loads(msg[TUPLE_MSG])
                                    print(
                                        f'Long record ({duration/60:.1f} min): {loggingID(record)}'
                                    )
                            if numStuck >= executor._max_workers:
                                print(
                                    f"All {executor._max_workers} threads are stuck on long running records"
                                )

                pauseSeconds = governor.govern()
                # either governor fully triggered or our executor is full
                # not going to get more messages
                if pauseSeconds < 0.0:
                    time.sleep(1)
                    continue
                if len(futures) >= executor._max_workers:
                    time.sleep(1)
                    continue
                if pauseSeconds > 0.0:
                    time.sleep(pauseSeconds)

                if empty_pause:
                    if time.time() < empty_pause:
                        time.sleep(1)
                        continue
                    empty_pause = 0

                while len(futures) < executor._max_workers:
                    try:
                        response = bytearray()
                        g2.getRedoRecord(response)
                        # print(response)
                        if not response:
                            print(
                                f"No redo records available. Pausing for {EMPTY_PAUSE_TIME} seconds."
                            )
                            empty_pause = time.time() + EMPTY_PAUSE_TIME
                            break
                        msg = response.decode()
                        futures[executor.submit(process_msg, g2, msg, args.info)] = (
                            msg,
                            time.time(),
                        )
                    except Exception as err:
                        print(f"{type(err).__name__}: {err}", file=sys.stderr)
                        raise

            print(f"Processed total of {messages} redo")

        except Exception as err:
            print(
                f"{type(err).__name__}: Shutting down due to error: {err}",
                file=sys.stderr,
            )
            traceback.print_exc()
            nowTime = time.time()
            for fut, msg in futures.items():
                if not fut.done():
                    duration = nowTime - msg[TUPLE_STARTTIME]
                    record = orjson.loads(msg[TUPLE_MSG])
                    print(
                        f'Still processing ({duration/60:.1f} min: {loggingID(record)}'
                    )
            executor.shutdown()
            exit(-1)

except Exception as err:
    print(err, file=sys.stderr)
    traceback.print_exc()
    exit(-1)
