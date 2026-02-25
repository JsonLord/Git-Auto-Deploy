import collections
try:
    from collections.abc import MutableMapping
except ImportError:
    from collections import MutableMapping
from ..wrappers import GitWrapper
from ..lock import Lock
from ..events import DeployEvent


class Project(MutableMapping):

    """A dictionary that applies an arbitrary key-altering
       function before accessing the keys"""

    def __init__(self, *args, **kwargs):
        self.store = dict()
        self.update(dict(*args, **kwargs))  # use the free update to set keys

    def __getitem__(self, key):
        return self.store[self.__keytransform__(key)]

    def __setitem__(self, key, value):
        self.store[self.__keytransform__(key)] = value

    def __delitem__(self, key):
        del self.store[self.__keytransform__(key)]

    def __iter__(self):
        return iter(self.store)

    def __len__(self):
        return len(self.store)

    def __keytransform__(self, key):
        return key

    def get_name(self):
        return self['url'].split('/')[-1].split('.git')[0]

    def passes_payload_filter(self, payload, action):

        # At least one filter must match
        for filter in self['payload-filter']:

            # All options specified in the filter must match
            for filter_key, filter_value in filter.items():

                # Ignore filters with value None (let them pass)
                if filter_value == None:
                    continue

                # Interpret dots in filter name as path notations 
                node_value = payload
                for node_key in filter_key.split('.'):

                    # If the path is not valid the filter does not match
                    if not node_key in node_value:
                        action.log_info("Filter '%s' does not match since the path is invalid" % (filter_key))

                        # Filter does not match, do not process this repo config
                        return False

                    node_value = node_value[node_key]

                if filter_value == node_value:
                    continue

                # If the filter value is set to True. the filter
                # will pass regardless of the actual value
                if filter_value == True:
                    continue

                action.log_debug("Filter '%s' does not match ('%s' != '%s')" % (filter_key, filter_value, (str(node_value)[:75] + '..') if len(str(node_value)) > 75 else str(node_value)))

                # Filter does not match, do not process this repo config
                return False

        # Filter does match, proceed
        return True

    def passes_header_filter(self, request_headers):

        # At least one filter must match
        for key in self['header-filter']:

            # Verify that the request has the required header attribute
            if key.lower() not in request_headers:
                return False

            # "True" indicates that any header value is accepted
            if self['header-filter'][key] is True:
                continue

            # Verify that the request has the required header value
            if self['header-filter'][key] != request_headers[key.lower()]:
                return False

        # Filter does match, proceed
        return True

    def apply_filters(self, request_headers, request_body, action):
        """Verify that the suggested repositories has matching settings and
        issue git pull and/or deploy commands."""
        import os
        import time
        import json

        payload = json.loads(request_body)

        # Verify that all payload filters matches the request (if any payload filters are specified)
        if 'payload-filter' in self and not self.passes_payload_filter(payload, action):

            # Filter does not match, do not process this repo config
            return False

        # Verify that all header filters matches the request (if any header filters are specified)
        if 'header-filter' in self and not self.passes_header_filter(request_headers):

            # Filter does not match, do not process this repo config
            return False

        return True

    def execute_webhook(self, event_store):
        """Verify that the suggested repositories has matching settings and
        issue git pull and/or deploy commands."""
        import os
        import time
        import json

        event = DeployEvent(self)
        event_store.register_action(event)
        event.set_waiting(True)
        event.log_info("Running deploy commands")

        # In case there is no path configured for the repository, no pull will
        # be made.
        if 'path' not in self:
            res = GitWrapper.deploy(self)
            event.log_info("%s" % res)
            event.set_waiting(False)
            event.set_success(True)
            return

        # If the path does not exist, a warning will be raised and no pull or
        # deploy will be made.
        if not os.path.isdir(self['path']):
            event.log_error("The repository '%s' does not exist locally. Make sure it was pulled properly without errors by reviewing the log." % self['path'])
            event.set_waiting(False)
            event.set_success(False)
            return

        # If the path is not writable, a warning will be raised and no pull or
        # deploy will be made.
        if not os.access(self['path'], os.W_OK):
            event.log_error("The path '%s' is not writable. Make sure that GAD has write access to that path." % self['path'])
            event.set_waiting(False)
            event.set_success(False)
            return

        running_lock = Lock(os.path.join(self['path'], 'status_running'))
        waiting_lock = Lock(os.path.join(self['path'], 'status_waiting'))
        try:

            # Attempt to obtain the status_running lock
            while not running_lock.obtain():

                # If we're unable, try once to obtain the status_waiting lock
                if not waiting_lock.has_lock() and not waiting_lock.obtain():
                    event.log_error("Unable to obtain the status_running lock nor the status_waiting lock. Another process is already waiting, so we'll ignore the request.")

                    # If we're unable to obtain the waiting lock, ignore the request
                    break

                # Keep on attempting to obtain the status_running lock until we succeed
                time.sleep(5)

            n = 4
            res = None
            while n > 0:

                # Attempt to pull up a maximum of 4 times
                res = GitWrapper.pull(self)

                # Return code indicating success?
                if res == 0:
                    break

                n -= 1

            if 0 < n:
                res = GitWrapper.deploy(self)

                # Check for failures and report to Jules if configured
                for r in res:
                    if r != 0:
                        self.report_failure_to_jules(r)
                        break

        #except Exception as e:
        #    logger.error('Error during \'pull\' or \'deploy\' operation on path: %s' % self['path'])
        #    logger.error(e)
        #    raise e

        finally:

            # Release the lock if it's ours
            if running_lock.has_lock():
                running_lock.release()

            # Release the lock if it's ours
            if waiting_lock.has_lock():
                waiting_lock.release()

        event.log_info("Deploy commands were executed")
        event.set_waiting(False)
        event.set_success(True)

    def report_failure_to_jules(self, result):
        """Report a deployment failure to Jules by creating a GitHub issue."""
        import subprocess
        import os
        import logging
        logger = logging.getLogger()

        if not self.get('report_to_jules', False):
            return

        repo = self.get('github_repo')
        if not repo:
            # Try to extract from URL
            import re
            match = re.search(r'github\.com[:/]([^/]+/[^/.]+)(\.git)?', self['url'])
            if match:
                repo = match.group(1)
            else:
                logger.warning("Could not identify GitHub repository for reporting to Jules")
                return

        branch = self.get('payload_branch', self.get('branch', 'master'))
        space_id = self.get('huggingface_space', 'unknown')

        # Get error message from stderr, or stdout if stderr is empty
        error_msg = ""
        if hasattr(result, 'stderr') and result.stderr:
            error_msg = result.stderr
        elif hasattr(result, 'stdout') and result.stdout:
            error_msg = result.stdout
        else:
            error_msg = "Unknown error (exit code %s)" % result

        # Call the reporting script.
        # The script is expected to be in the 'scripts' directory.
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        script_path = os.path.join(base_dir, 'scripts', 'report_to_jules.py')

        if not os.path.exists(script_path):
            logger.error("Reporting script not found at %s" % script_path)
            return

        cmd = [
            'python3', script_path,
            '--repo', repo,
            '--branch', branch,
            '--error-msg', error_msg,
            '--space-id', space_id
        ]

        logger.info("Reporting failure to Jules for repo %s, branch %s" % (repo, branch))

        # We assume GITHUB_TOKEN is available in the environment
        try:
            subprocess.run(cmd, check=False)
        except Exception as e:
            logger.error("Failed to run reporting script: %s" % e)
