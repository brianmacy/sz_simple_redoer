# sz_simple_redoer

## Overview
Simple parallel redo processor using the Senzing API and is meant to provide developers with a simple starting point for a simple, scalable redo processor.  This took 15minutes to adapt from the sz_sqs_consumer project.

It is a limited function [more or less] drop in replacement for the senzing/redoer.


### Required parameter (environment)
```
SENZING_ENGINE_CONFIGURATION_JSON
```

### Optional parameters (environment)
```
SENZING_LOG_LEVEL (default: info)
SENZING_THREADS_PER_PROCESS (default: based on whatever concurrent.futures.ThreadPoolExecutor chooses automatically)
SENZING_REDO_SLEEP_TIME_IN_SECONDS (default: 60 seconds)
LONG_RECORD: (default: 300 seconds)
```

## Building/Running
```
docker build -t brian/sz_simple_redoer .
docker run --user $UID -it -e SENZING_ENGINE_CONFIGURATION_JSON brian/sz_simple_redoer
```

## Additional items to note
 * Will exit on non-data related exceptions after processing or failing to process the current records in flight
 * If a record takes more than 5min to process (LONG_RECORD), it will let you know which record it is and how long it has been processing
 * Does not use the senzing-###### format for log messages (unlike the senzing/redoer) and simply uses python `print` with strings.
 * Does not support "WithInfo" output to queues but you can provide a "-i" command line option that will enable printing the WithInfo responses out.  It is simple enough to code in whatever you want done with WithInfo messages in your solution.
