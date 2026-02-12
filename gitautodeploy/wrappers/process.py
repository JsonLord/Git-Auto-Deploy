class ProcessResult(int):
    def __new__(cls, returncode, stdout, stderr):
        return super(ProcessResult, cls).__new__(cls, returncode)
    def __init__(self, returncode, stdout, stderr):
        self.stdout = stdout
        self.stderr = stderr

class ProcessWrapper():
    """Wraps the subprocess popen method and provides logging."""

    def __init__(self):
        pass

    @staticmethod
    def call(*popenargs, **kwargs):
        """Run command with arguments. Wait for command to complete. Sends
        output to logging module. The arguments are the same as for the Popen
        constructor."""

        from subprocess import Popen, PIPE
        import logging
        logger = logging.getLogger()

        kwargs['stdout'] = PIPE
        kwargs['stderr'] = PIPE

        supressStderr = None
        if 'supressStderr' in kwargs:
            supressStderr = kwargs['supressStderr']
            del kwargs['supressStderr']

        p = Popen(*popenargs, **kwargs)

        stdout_accumulator = []
        stderr_accumulator = []

        import threading

        def handle_output(stream, accumulator, is_stderr):
            for line in iter(stream.readline, b''):
                decoded_line = line.decode("utf-8").rstrip()
                accumulator.append(decoded_line)
                if is_stderr and not supressStderr:
                    logger.error(decoded_line)
                else:
                    logger.info(decoded_line)
            stream.close()

        t1 = threading.Thread(target=handle_output, args=(p.stdout, stdout_accumulator, False))
        t2 = threading.Thread(target=handle_output, args=(p.stderr, stderr_accumulator, True))

        t1.start()
        t2.start()

        p.wait()
        t1.join()
        t2.join()

        stdout = "\n".join(stdout_accumulator)
        stderr = "\n".join(stderr_accumulator)

        return ProcessResult(p.returncode, stdout, stderr)
