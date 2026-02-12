import argparse
import os
import requests
import sys
import re
from datetime import datetime, timedelta, timezone

def scrub_tokens(text):
    if not isinstance(text, str):
        return text

    # Patterns for common tokens
    patterns = [
        (r'hf_[a-zA-Z0-9]{20,}', '[HF_TOKEN_REDACTED]'),
        (r'ghp_[a-zA-Z0-9]{20,}', '[GH_TOKEN_REDACTED]'),
        (r'github_pat_[a-zA-Z0-9_]{20,}', '[GH_PAT_REDACTED]'),
        (r'sk-[a-zA-Z0-9]{30,}', '[OPENAI_TOKEN_REDACTED]'),
        (r'glpat-[a-zA-Z0-9\-]{20,}', '[GITLAB_TOKEN_REDACTED]'),
        # Generic credential in URL
        (r'https?://[^:\s]+:[^@\s]+@', 'https://[CREDENTIALS_REDACTED]@')
    ]

    scrubbed = text
    for pattern, replacement in patterns:
        scrubbed = re.sub(pattern, replacement, scrubbed)

    return scrubbed

def main():
    parser = argparse.ArgumentParser(description='Report failure to Jules via GitHub Issues')
    parser.add_argument('--repo', required=True, help='GitHub repository (owner/repo)')
    parser.add_argument('--branch', required=True, help='Branch that failed')
    parser.add_argument('--error-msg', required=True, help='Error message or logs')
    parser.add_argument('--token', help='GitHub API token')
    parser.add_argument('--space-id', help='Hugging Face Space ID')

    args = parser.parse_args()

    token = args.token or os.environ.get('GITHUB_TOKEN') or os.environ.get('GITHUB_API_KEY')
    if not token:
        print("Error: GitHub token not provided and GITHUB_TOKEN or GITHUB_API_KEY env var not set.")
        sys.exit(1)

    issue_title = f"HF Space deploy failed for branch {args.branch}"

    # Scrub tokens from error message
    safe_error_msg = scrub_tokens(args.error_msg)

    report_content = f"""
### New Deployment Failure Logs:
**Timestamp:** {datetime.now(timezone.utc).isoformat()}
**Space ID:** {args.space_id}
**Branch:** {args.branch}

```
{safe_error_msg}
```
"""

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    # 0. Try to ensure issues are enabled
    try:
        repo_url = f"https://api.github.com/repos/{args.repo}"
        repo_resp = requests.get(repo_url, headers=headers, timeout=10)
        if repo_resp.status_code == 200:
            repo_info = repo_resp.json()
            if not repo_info.get('has_issues'):
                print(f"Issues are disabled for {args.repo}. Attempting to enable them...")
                requests.patch(repo_url, json={"has_issues": True}, headers=headers, timeout=10)
    except Exception as e:
        print(f"Warning: Failed to check/enable issues: {e}")

    # 1. Search for existing open issue with the same title and jules:run label
    existing_issue = None
    try:
        query_url = f"https://api.github.com/repos/{args.repo}/issues?state=open&labels=jules:run"
        resp = requests.get(query_url, headers=headers, timeout=10)
        if resp.status_code == 200:
            issues = resp.json()
            for issue in issues:
                if issue['title'] == issue_title:
                    created_at = datetime.strptime(issue['created_at'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) - created_at < timedelta(hours=24):
                        existing_issue = issue
                        break
    except Exception as e:
        print(f"Warning: Failed to check for existing issues: {e}")

    if existing_issue:
        # 2. Add comment to existing issue
        print(f"Found existing issue #{existing_issue['number']}. Adding comment...")
        comment_url = existing_issue['comments_url']
        resp = requests.post(comment_url, json={"body": report_content}, headers=headers)
        if resp.status_code == 201:
            print(f"Successfully added comment to issue #{existing_issue['number']}")
            sys.exit(0)
        else:
            print(f"Failed to add comment: {resp.status_code} {resp.text}")
            # Fallback to creating a new issue if comment fails? Better not, just exit.
            sys.exit(1)
    else:
        # 3. Create new issue
        print("No recent existing issue found. Creating new one...")
        issue_body = f"""
## Deployment Failure Report

**Space ID:** {args.space_id}
**Branch:** {args.branch}
**Status:** Failed

{report_content}

Jules, please fix the issues in this branch and redeploy.
"""
        url = f"https://api.github.com/repos/{args.repo}/issues"
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
