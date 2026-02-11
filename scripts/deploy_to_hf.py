import argparse
import os
import time
import sys
import shutil
import requests
from huggingface_hub import HfApi

def get_hf_logs(space_id, token, log_type="build"):
    url = f"https://huggingface.co/api/spaces/{space_id}/logs/{log_type}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.text
        else:
            return f"Failed to fetch {log_type} logs: {response.status_code}"
    except Exception as e:
        return f"Error fetching {log_type} logs: {e}"

def main():
    parser = argparse.ArgumentParser(description='Deploy to Hugging Face Spaces')
    parser.add_argument('--repo-path', required=True, help='Path to the local repository')
    parser.add_argument('--space-id', required=True, help='Hugging Face Space ID (e.g., user/space-name)')
    parser.add_argument('--branch', default='main', help='Branch to deploy')
    parser.add_argument('--token', help='Hugging Face API token')
    parser.add_argument('--create', action='store_true', help='Create the space if it does not exist')
    parser.add_argument('--sdk', default='static', help='SDK for the new space (if created)')

    args = parser.parse_args()

    # Prioritize HF_TOKEN as it's common in Spaces
    token = args.token or os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN')
    if not token:
        print("Error: Hugging Face token not provided and HF_TOKEN or HUGGING_FACE_HUB_TOKEN env var not set.")
        sys.exit(1)

    api = HfApi(token=token)

    try:
        if args.create:
            try:
                api.repo_info(repo_id=args.space_id, repo_type="space")
                print(f"Space {args.space_id} already exists.")
            except Exception:
                print(f"Creating new Space: {args.space_id}")
                api.create_repo(
                    repo_id=args.space_id,
                    repo_type="space",
                    space_sdk=args.sdk,
                    private=False
                )

        # Check if README.md exists in repo-path, if not, try to use the server's README.md as template
        readme_path = os.path.join(args.repo_path, 'README.md')
        if not os.path.exists(readme_path):
            server_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            fallback_readme = os.path.join(server_root, 'README.md')
            if os.path.exists(fallback_readme):
                print(f"README.md not found in {args.repo_path}. Using server README.md as template.")
                shutil.copy(fallback_readme, readme_path)

        print(f"Uploading to Hugging Face Space: {args.space_id} from {args.repo_path}")

        api.upload_folder(
            folder_path=args.repo_path,
            repo_id=args.space_id,
            repo_type="space",
            path_in_repo="",
            commit_message=f"Deploy branch {args.branch} via Git-Auto-Deploy",
            delete_patterns="*",
        )

        print("Upload successful. Waiting for build...")

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

                    log_type = "build" if status == "BUILD_ERROR" else "run"
                    logs = get_hf_logs(args.space_id, token, log_type)

                    print(f"--- {log_type.capitalize()} Logs ---")
                    print(logs)
                    print("--------------------------")
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
