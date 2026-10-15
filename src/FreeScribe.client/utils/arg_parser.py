import argparse
import utils.log_config

# The parsed command line (set by parse_args)
args = argparse.Namespace(file_debug=False, self_test=None, self_test_report=None)


def parse_args():
    global args
    parser = argparse.ArgumentParser()
    parser.add_argument("--file-debug", help="Enable the file logger", action="store_true")
    parser.add_argument("--self-test", metavar="AUDIO.wav",
                        help="Check the install: transcribe this audio, write a note, save a report and exit")
    parser.add_argument("--self-test-report", metavar="REPORT.json",
                        help="Where to save the self-test report (default: next to the audio file)")
    # Ignore what the operating system may add (macOS can pass -psn_...)
    args, _unknown = parser.parse_known_args()

    if args.file_debug:
        utils.log_config.add_file_handler(utils.log_config.logger, utils.log_config.formatter)
    return args
