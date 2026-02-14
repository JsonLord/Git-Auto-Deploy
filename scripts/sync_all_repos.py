import os
import requests
import argparse
import sys
import json

def get_all_github_repos(token):
    repos = []
    url = "https://api.github.com/user/repos?per_page=100&type=owner"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    while url:
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code != 200:
                print(f"Error fetching repos: {response.status_code} {response.text}")
                break

            repos.extend(response.json())

            # Handle pagination
            if 'next' in response.links:
                url = response.links['next']['url']
            else:
                url = None
        except Exception as e:
            print(f"Exception fetching repos: {e}")
            break

    return repos

def main():
    parser = argparse.ArgumentParser(description='Sync all GitHub repos to Git-Auto-Deploy')
    parser.add_argument('--gad-url', default='http://localhost:7860', help='URL of the GAD server')
    parser.add_argument('--token', help='GitHub API token')
    parser.add_argument('--limit', type=int, default=100, help='Maximum number of repos to sync')
    parser.add_argument('--inject-actions', action='store_true', help='Inject GitHub Actions for automatic sync')
    parser.add_argument('--detect-newest', action='store_true', default=True, help='Detect and use the newest branch for each repo')

    args = parser.parse_args()

    token = args.token or os.environ.get('GITHUB_API_KEY') or os.environ.get('GITHUB_TOKEN')
    if not token:
        print("Error: GitHub token not provided and GITHUB_API_KEY or GITHUB_TOKEN env var not set.")
        sys.exit(1)

    print("Fetching repositories from GitHub...")
    github_repos = get_all_github_repos(token)
    print(f"Found {len(github_repos)} repositories.")

    count = 0
    for repo in github_repos[:args.limit]:
        repo_url = repo['clone_url']
        repo_name = repo['full_name']

        print(f"[{count+1}/{len(github_repos)}] Registering {repo_name}...")

        try:
            # Call GAD API
            resp = requests.post(
                f"{args.gad_url}/api/repo/add",
                json={
                    "url": repo_url,
                    "inject_actions": args.inject_actions,
                    "detect_newest": args.detect_newest
                },
                timeout=30
            )

            if resp.status_code == 200:
                data = resp.json()
                if data.get('success'):
                    print(f"  Successfully registered: {data.get('message')}")
                    count += 1
                else:
                    print(f"  Skipped: {data.get('message')}")
            else:
                print(f"  Failed to register {repo_name}: {resp.status_code} {resp.text}")

        except Exception as e:
            print(f"  Error registering {repo_name}: {e}")

    print(f"Finished. Successfully onboarded {count} repositories.")

if __name__ == "__main__":
    main()
