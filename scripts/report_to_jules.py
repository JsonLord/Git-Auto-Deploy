import argparse
import os
import requests
import sys

def main():
    parser = argparse.ArgumentParser(description='Report failure to Jules via GitHub Issues')
    parser.add_argument('--repo', required=True, help='GitHub repository (owner/repo)')
    parser.add_argument('--branch', required=True, help='Branch that failed')
    parser.add_argument('--error-msg', required=True, help='Error message or logs')
    parser.add_argument('--token', help='GitHub API token')
    parser.add_argument('--space-id', help='Hugging Face Space ID')

    args = parser.parse_args()

    token = args.token or os.environ.get('GITHUB_TOKEN')
    if not token:
        print("Error: GitHub token not provided and GITHUB_TOKEN env var not set.")
        sys.exit(1)

    issue_title = f"HF Space deploy failed for branch {args.branch}"
    issue_body = f"""
## Deployment Failure Report

**Space ID:** {args.space_id}
**Branch:** {args.branch}
**Status:** Failed

### Logs / Error Message:
```
{args.error_msg}
```

Jules, please fix the issues in this branch and redeploy.
"""

    url = f"https://api.github.com/repos/{args.repo}/issues"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    data = {
        "title": issue_title,
        "body": issue_body,
        "labels": ["jules:run"]
    }

    response = requests.post(url, json=data, headers=headers)

    if response.status_code == 201:
        print(f"Successfully created GitHub issue: {response.json().get('html_url')}")
    else:
        print(f"Failed to create GitHub issue: {response.status_code} {response.text}")
        sys.exit(1)

if __name__ == "__main__":
    main()
