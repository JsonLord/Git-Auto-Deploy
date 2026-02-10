import argparse
import os
import subprocess
import time
import sys
from huggingface_hub import HfApi

def main():
    parser = argparse.ArgumentParser(description='Deploy to Hugging Face Spaces')
    parser.add_argument('--repo-path', required=True, help='Path to the local repository')
    parser.add_argument('--space-id', required=True, help='Hugging Face Space ID (e.g., user/space-name)')
    parser.add_argument('--branch', default='main', help='Branch to deploy')
    parser.add_argument('--token', help='Hugging Face API token')

    args = parser.parse_args()

    token = args.token or os.environ.get('HUGGING_FACE_HUB_TOKEN')
    if not token:
        print("Error: Hugging Face token not provided and HUGGING_FACE_HUB_TOKEN env var not set.")
        sys.exit(1)

    api = HfApi(token=token)

    try:
        print(f"Pushing to Hugging Face Space: {args.space_id} branch: {args.branch}")

        # Using oauth2 as username is a common pattern for token-based auth
        remote_url = f"https://oauth2:{token}@huggingface.co/spaces/{args.space_id}"

        # Check if remote 'hf' exists
        check_remote = subprocess.run(["git", "remote", "get-url", "hf"], cwd=args.repo_path, capture_output=True)

        if check_remote.returncode == 0:
            # Update existing remote
            subprocess.run(["git", "remote", "set-url", "hf", remote_url], cwd=args.repo_path, check=True)
        else:
            # Add new remote
            subprocess.run(["git", "remote", "add", "hf", remote_url], cwd=args.repo_path, check=True)

        # Push to HF. We push the local branch to the remote's main branch.
        result = subprocess.run(["git", "push", "hf", f"{args.branch}:main", "--force"], cwd=args.repo_path, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"Error pushing to HF: {result.stderr}")
            sys.exit(1)

        print("Push successful. Waiting for build...")

        # Poll for status
        max_retries = 30
        retry_interval = 20 # seconds

        for i in range(max_retries):
            try:
                runtime_info = api.get_space_runtime(repo_id=args.space_id)
                status = runtime_info.stage
                print(f"Current status: {status}")

                if status == "RUNNING":
                    print("Deployment successful!")
                    sys.exit(0)
                elif status in ["BUILD_ERROR", "RUNTIME_ERROR", "DEVSERVER_ERROR"]:
                    print(f"Deployment failed with status: {status}")
                    # Try to get logs
                    try:
                        # Attempt to get logs using API
                        # In some versions of huggingface_hub, it might be different
                        logs = api.get_space_logs(repo_id=args.space_id)
                        print("--- Build/Runtime Logs ---")
                        print(logs)
                        print("--------------------------")
                    except Exception as e:
                        print(f"Could not fetch logs: {e}")
                    sys.exit(2)
            except Exception as e:
                print(f"Error polling status: {e}")

            time.sleep(retry_interval)

        print("Timed out waiting for deployment.")
        sys.exit(3)

    except Exception as e:
        print(f"An error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
