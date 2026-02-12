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

def check_file_sizes(repo_path, limit_mb=10):
    limit_bytes = limit_mb * 1024 * 1024
    large_files = []

    for root, dirs, files in os.walk(repo_path):
        if '.git' in dirs:
            dirs.remove('.git')

        for name in files:
            filepath = os.path.join(root, name)
            try:
                size = os.path.getsize(filepath)
                if size > limit_bytes:
                    rel_path = os.path.relpath(filepath, repo_path)
                    large_files.append((rel_path, size))
            except OSError:
                continue

    return large_files

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
    token = args.token or os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN') or os.environ.get('hf_token')

    if not token:
        print("Error: Hugging Face token not provided and HF_TOKEN, HUGGING_FACE_HUB_TOKEN, or hf_token env var not set.")
        sys.exit(1)

    # Redact token for logging
    masked_token = token[:4] + "..." + token[-4:] if len(token) > 8 else "***"
    print(f"Using Hugging Face token: {masked_token}")

    api = HfApi(token=token)

    try:
        # Check for large files (> 10MB) as required by HF Spaces without LFS
        print(f"Checking for files larger than 10MB in {args.repo_path}...")
        large_files = check_file_sizes(args.repo_path)
        if large_files:
            print("ERROR: Deployment aborted. The following files exceed the 10MB limit:")
            for path, size in large_files:
                print(f"  - {path} ({size / (1024*1024):.2f} MB)")
            print("\nHugging Face Spaces requires Git LFS for files larger than 10MB.")
            print("Please either:")
            print("  1. Use Git LFS to track these files.")
            print("  2. Reduce the file size.")
            print("  3. Remove the large files.")
            sys.exit(4) # Specific exit code for large file error

        # Diagnostic: Who am I?
        try:
            user_info = api.whoami()
            username = user_info.get('name')
            orgs = [org.get('name') for org in user_info.get('orgs', [])]
            print(f"Authenticated as: {username}")
            if orgs:
                print(f"Member of organizations: {', '.join(orgs)}")

            # Check if we have access to the namespace
            target_namespace = args.space_id.split('/')[0] if '/' in args.space_id else None
            if target_namespace and target_namespace != username and target_namespace not in orgs:
                print(f"Warning: Target namespace '{target_namespace}' is not your username and not in your organizations.")
                print(f"This might lead to 403 Forbidden errors if you don't have write access.")
        except Exception as diag_e:
            print(f"Warning: Could not fetch user info for diagnostics: {diag_e}")

        # Detect SDK
        sdk = args.sdk
        if os.path.exists(os.path.join(args.repo_path, 'Dockerfile')):
            sdk = 'docker'
            print(f"Dockerfile detected. Using 'docker' SDK.")

        if args.create:
            try:
                api.repo_info(repo_id=args.space_id, repo_type="space")
                print(f"Space {args.space_id} already exists.")
            except Exception:
                print(f"Creating new Space: {args.space_id}")
                api.create_repo(
                    repo_id=args.space_id,
                    repo_type="space",
                    space_sdk=sdk,
                    private=False
                )

        # Check if README.md exists in repo-path
        readme_path = os.path.join(args.repo_path, 'README.md')
        content = ""
        if os.path.exists(readme_path):
            with open(readme_path, 'r') as f:
                content = f.read()

        # If README is missing or doesn't have metadata, inject it
        if not content.strip().startswith('---'):
            print(f"Injecting Hugging Face Space metadata into README.md")
            title = args.space_id.split('/')[-1].replace('-', ' ').title()
            metadata = f"---\ntitle: {title}\nemoji: 🚀\ncolorFrom: blue\ncolorTo: green\nsdk: {sdk}\npinned: false\n---\n\n"

            # If no README at all, use server README as base if available
            if not content:
                server_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                fallback_readme = os.path.join(server_root, 'README.md')
                if os.path.exists(fallback_readme):
                    with open(fallback_readme, 'r') as f:
                        content = f.read()

            with open(readme_path, 'w') as f:
                f.write(metadata + content)

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
