import os
import sys
import argparse
import time
import requests
import json
import re
import subprocess
import csv
from huggingface_hub import HfApi
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from gradio_client import Client

# Absolute path to the scripts directory of Git-Auto-Deploy
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(BASE_DIR, 'scripts')

@tool
def execute_bash(command: str) -> str:
    """Execute a bash command in the repository directory and return the output.
    Use this sparingly and only for necessary deployment tasks like running tests or building artifacts."""
    try:
        # Command is executed in the current working directory of the script
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120)
        return f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}\nReturn Code: {result.returncode}"
    except Exception as e:
        return f"Error executing command: {str(e)}"

def scan_for_endpoints(repo_path):
    """Scan the code for potential API endpoints."""
    endpoints = []
    patterns = [
        (r'@app\.(?:route|get|post|put|delete)\([\'"]([^\'"]+)[\'"]', "Flask/FastAPI Route"),
        (r'router\.(?:get|post|put|delete)\([\'"]([^\'"]+)[\'"]', "FastAPI/Express Route"),
        (r'gr\.Interface', "Gradio Interface"),
        (r'gr\.ChatInterface', "Gradio ChatInterface"),
        (r'st\.[\w_]+', "Streamlit Component"),
        (r'app = FastAPI\(\)', "FastAPI App"),
        (r'app = Flask\(', "Flask App")
    ]

    for root, _, files in os.walk(repo_path):
        if '.git' in root or '__pycache__' in root:
            continue
        for file in files:
            if file.endswith('.py'):
                filepath = os.path.join(root, file)
                try:
                    with open(filepath, 'r') as f:
                        content = f.read()
                        for pattern, desc in patterns:
                            matches = re.findall(pattern, content)
                            for match in matches:
                                endpoints.append({"file": file, "match": match, "type": desc})
                except Exception:
                    continue
    return endpoints

def test_endpoint(url, endpoint_path=None, method='GET', data=None):
    """Test a specific endpoint."""
    full_url = url
    if endpoint_path:
        full_url = f"{url.rstrip('/')}/{endpoint_path.lstrip('/')}"

    print(f"Testing {full_url}...")
    try:
        if method.upper() == 'GET':
            resp = requests.get(full_url, timeout=30)
        else:
            resp = requests.post(full_url, json=data, timeout=30)

        return {
            "status": resp.status_code,
            "success": 200 <= resp.status_code < 300,
            "response": resp.text[:500] if resp.text else ""
        }
    except Exception as e:
        return {
            "status": "Error",
            "success": False,
            "error": str(e)
        }

def get_hf_space_url(space_id):
    if '/' not in space_id:
        return f"https://huggingface.co/spaces/{space_id}"
    user, name = space_id.split('/')
    return f"https://{user}-{name.replace('_', '-').replace('.', '-')}.hf.space"

def save_log_sheet(log_sheet, repo_path):
    sheet_path = os.path.join(repo_path, 'api_test_results.csv')
    with open(sheet_path, 'w', newline='') as csvfile:
        fieldnames = ['endpoint', 'status', 'success', 'details', 'classification']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for entry in log_sheet:
            writer.writerow(entry)
    return sheet_path

def main():
    parser = argparse.ArgumentParser(description='Agentic Deployment to Hugging Face')
    parser.add_argument('--repo-path', required=True, help='Path to the local repository')
    parser.add_argument('--space-id', required=True, help='Hugging Face Space ID')
    parser.add_argument('--token', help='Hugging Face Token')
    parser.add_argument('--openai-token', help='OpenAI API Key (Blablador)')
    parser.add_argument('--github-token', help='GitHub Token for Jules reporting')
    parser.add_argument('--github-repo', help='GitHub Repo (owner/repo)')
    parser.add_argument('--branch', default='master', help='Branch')

    args = parser.parse_args()

    # Prioritize tokens
    openai_token = args.openai_token or os.environ.get('BLABLADOR_API_KEY')
    hf_token = args.token or os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN')
    github_token = args.github_token or os.environ.get('GITHUB_TOKEN') or os.environ.get('GITHUB_API_KEY')

    if not openai_token:
        print("Error: OpenAI token (BLABLADOR_API_KEY) not found. This is required for the LLM call.")
        sys.exit(1)

    # Initialize LLM
    llm = ChatOpenAI(
        openai_api_key=openai_token,
        openai_api_base="https://api.helmholtz-blablador.fz-juelich.de/v1",
        model_name="alias-code"
    )

    hf_docs_context = """
HUGGING FACE SPACES DOCUMENTATION REFERENCE:
1. README.md YAML Metadata:
   Every Space needs a README.md with a YAML block at the top.
   ---
   title: My Space Title
   emoji: 🚀
   colorFrom: [MUST BE one of: red, yellow, green, blue, indigo, purple, pink, gray]
   colorTo: [MUST BE one of: red, yellow, green, blue, indigo, purple, pink, gray]
   sdk: gradio | streamlit | docker | static
   app_file: app.py (for gradio/streamlit)
   app_port: 7860 (for docker)
   pinned: false
   ---

2. SDK Specifics:
   - Gradio: Requires `gradio` in requirements.txt. Main file is usually app.py. API endpoints are automatically created.
   - Streamlit: Requires `streamlit` in requirements.txt. Main file is app.py.
   - Docker: Requires a Dockerfile. MUST expose port 7860. The server inside MUST listen on 0.0.0.0:7860.
     Example Dockerfile:
     FROM python:3.12
     WORKDIR /app
     COPY . .
     RUN pip install -r requirements.txt
     EXPOSE 7860
     CMD ["python", "app.py"]

3. API Access:
   HF Spaces tunnel port 7860 to the public URL: https://user-space.hf.space
   For Docker SDK, any HTTP server on 7860 is accessible.
   For Gradio SDK, the /api/predict and other endpoints are available.
"""

    # Create Agent with real filesystem backend
    repo_abs_path = os.path.abspath(args.repo_path)
    # virtual_mode=False ensures changes are written to the actual disk
    backend = FilesystemBackend(root_dir=repo_abs_path, virtual_mode=False)

    agent = create_deep_agent(
        model=llm,
        tools=[execute_bash],
        backend=backend,
        system_prompt=f"You are an expert software engineer specialized in Hugging Face Spaces. {hf_docs_context}. Your mission is to iteratively analyze the code in the current directory and adapt it to work perfectly as a HF Space. Ensure the README.md is correct, dependencies are in requirements.txt, and a clear entry point exists. If it's a backend, ensure it uses port 7860. CRITICAL: You MUST use one of the allowed colors for colorFrom and colorTo in README.md metadata.",
        debug=True
    )

    print("--- Phase 1: Iterative Agentic Adaptation ---")
    adapt_instruction = f"1. Explore the repository. 2. Decide on the best SDK. 3. Update README.md with proper YAML metadata. 4. Create/Modify app.py or Dockerfile as needed. 5. Ensure all necessary dependencies are in requirements.txt. 6. Verify that an API endpoint will be exposed on port 7860."

    try:
        agent.invoke({"messages": [{"role": "user", "content": adapt_instruction}]})

        print("--- Phase 1.1: Verification/Reflection ---")
        verify_instruction = "Check the files you just modified. Does the README.md have the required YAML header? Is there an entry point? Are the ports correct? Ensure everything is committed to disk."
        agent.invoke({"messages": [{"role": "user", "content": verify_instruction}]})
    except Exception as e:
        print(f"Agentic adaptation failed: {e}")

    # Phase 1.2: Change Detection
    print("--- Phase 1.2: Change Detection ---")
    repo_path = os.path.abspath(args.repo_path)
    git_status = subprocess.run(['git', 'status', '--porcelain'], cwd=repo_path, capture_output=True, text=True).stdout.strip()

    new_branch = args.branch
    if git_status:
        print("Changes detected in the repository. Committing and pushing to a new branch.")

        timestamp = int(time.time())
        new_branch = f"agent-adaptation-{timestamp}"

        try:
            # Configure git user if not set
            subprocess.run(['git', 'config', 'user.name', 'Agentic Deployer'], cwd=repo_path)
            subprocess.run(['git', 'config', 'user.email', 'agent@example.com'], cwd=repo_path)

            # Create and switch to new branch
            subprocess.run(['git', 'checkout', '-b', new_branch], cwd=repo_path, check=True)

            # Add and commit
            subprocess.run(['git', 'add', '.'], cwd=repo_path, check=True)
            subprocess.run(['git', 'commit', '-m', 'Auto-adaptation for Hugging Face Spaces'], cwd=repo_path, check=True)

            # Push to GitHub if token and repo are available
            if github_token and args.github_repo:
                print(f"Pushing new branch {new_branch} to GitHub...")
                # Construct authenticated URL
                auth_url = f"https://x-access-token:{github_token}@github.com/{args.github_repo}.git"
                subprocess.run(['git', 'push', auth_url, new_branch], cwd=repo_path, check=True)
                print(f"Branch {new_branch} pushed successfully.")
            else:
                print("Skipping push to GitHub: missing token or repo info.")

        except Exception as git_e:
            print(f"Git operations failed: {git_e}")
            # Fallback to original branch for deployment if push fails?
            # Or continue with local changes on whatever branch we are.
    else:
        print("No changes detected.")

    print("--- Phase 2: Deployment ---")
    deploy_script = os.path.join(SCRIPTS_DIR, 'deploy_to_hf.py')
    deploy_cmd = [
        'python3', deploy_script,
        '--repo-path', args.repo_path,
        '--space-id', args.space_id,
        '--token', hf_token,
        '--branch', new_branch,
        '--create'
    ]
    subprocess.run(deploy_cmd, check=True)

    # Wait for Space to settle
    space_url = get_hf_space_url(args.space_id)
    print(f"Space URL: {space_url}")
    print("Waiting 60 seconds for build and startup...")
    time.sleep(60)

    print("--- Phase 3: API Discovery & Testing ---")
    log_sheet = []

    # Try discovery via Gradio first
    try:
        print(f"Attempting Gradio client discovery for {args.space_id}...")
        client = Client(args.space_id)
        api_info = client.view_api(return_format='dict')
        for ep in api_info.get('endpoints', []):
            log_sheet.append({
                "endpoint": f"Gradio: {ep}",
                "status": 200,
                "success": True,
                "details": str(ep),
                "classification": "OK"
            })
    except Exception as e:
        print(f"Gradio discovery skipped: {e}")

    # Generic Root test
    root_res = test_endpoint(space_url)
    log_sheet.append({
        "endpoint": "/",
        "status": root_res['status'],
        "success": root_res['success'],
        "details": str(root_res.get('response', root_res.get('error', ''))),
        "classification": "OK" if root_res['success'] else "FAILURE"
    })

    # Manual scan for more endpoints
    found = scan_for_endpoints(args.repo_path)
    for f in found:
        path = f['match']
        if isinstance(path, str) and path.startswith('/'):
            t_res = test_endpoint(space_url, path)
            log_sheet.append({
                "endpoint": path,
                "status": t_res['status'],
                "success": t_res['success'],
                "details": str(t_res.get('response', t_res.get('error', ''))),
                "classification": "OK" if t_res['success'] else "FAILURE"
            })

    sheet_path = save_log_sheet(log_sheet, args.repo_path)
    print(f"Log sheet saved to {sheet_path}")

    print("--- Phase 4: Failure Analysis & Jules Reporting ---")
    failures = [e for e in log_sheet if e['classification'] == 'FAILURE']
    if failures:
        fail_ctx = json.dumps(failures, indent=2)
        print(f"Analyzing {len(failures)} failures...")
        analysis_prompt = f"The following API endpoints failed on the HF Space: {fail_ctx}. Analyze if these are 'CODE_ERROR' (bug in app) or 'TEST_ERROR' (test misconfiguration). If CODE_ERROR, identify the fix."

        analysis = llm.invoke(analysis_prompt)
        print(f"Analysis: {analysis.content}")

        if "CODE_ERROR" in analysis.content.upper() and github_token and args.github_repo:
            print(f"Reporting CODE_ERROR to Jules for {args.github_repo}...")
            report_script = os.path.join(SCRIPTS_DIR, 'report_to_jules.py')
            report_cmd = [
                'python3', report_script,
                '--repo', args.github_repo,
                '--branch', args.branch,
                '--error-msg', f"API Failures:\n{fail_ctx}\n\nAnalysis:\n{analysis.content}",
                '--token', github_token,
                '--space-id', args.space_id
            ]
            subprocess.run(report_cmd, check=False)

if __name__ == "__main__":
    main()
