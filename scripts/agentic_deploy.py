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
from langchain_openai import ChatOpenAI
from langchain_community.agent_toolkits import FileManagementToolkit
from langchain_core.tools import tool
from gradio_client import Client

@tool
def execute_bash(command: str) -> str:
    """Execute a bash command and return the output."""
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=60)
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

    hf_token = args.token or os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN')
    openai_token = args.openai_token or os.environ.get('BLABLADOR_API_KEY')
    github_token = args.github_token or os.environ.get('GITHUB_TOKEN') or os.environ.get('GITHUB_API_KEY')

    if not openai_token:
        print("Error: OpenAI token (BLABLADOR_API_KEY) not found")
        sys.exit(1)

    # Initialize LLM
    llm = ChatOpenAI(
        openai_api_key=openai_token,
        openai_api_base="https://api.helmholtz-blablador.fz-juelich.de/v1",
        model_name="alias-code"
    )

    # File tools
    file_toolkit = FileManagementToolkit(
        root_dir=os.path.abspath(args.repo_path),
        selected_tools=["ls", "read_file", "write_file"]
    )
    tools = file_toolkit.get_tools() + [execute_bash]

    hf_docs = """
Hugging Face Spaces SDK Documentation:
- Gradio: sdk: gradio. Requires app.py. API endpoints are automatically created for each input/output.
- Streamlit: sdk: streamlit. Requires app.py.
- Docker: sdk: docker. Requires a Dockerfile. IMPORTANT: Port 7860 must be exposed and the server must listen on 0.0.0.0:7860. The HF URL will automatically tunnel to this port.
- README.md YAML Header:
  ---
  title: My Space Title
  emoji: 🚀
  colorFrom: blue
  colorTo: green
  sdk: [gradio/streamlit/docker]
  app_file: app.py (for gradio/streamlit)
  app_port: 7860 (for docker)
  ---
"""

    # Create Agent
    agent = create_deep_agent(
        model=llm,
        tools=tools,
        system_prompt=f"You are a specialized deployment agent. {hf_docs}. Your goal is to iteratively analyze and modify the repository at {args.repo_path} to make it a working Hugging Face Space. Ensure proper README.md metadata and entry points. Expose APIs and make them accessible via the public URL.",
        debug=True
    )

    print("--- Phase 1: Iterative Analysis & Modification ---")
    agent.invoke({"messages": [{"role": "user", "content": f"Analyze {args.repo_path} and adapt it for HF Space. If it's a backend app, expose it via a web server on port 7860. If it's Gradio, ensure app.py is ready."}]})

    print("--- Phase 2: Deployment ---")
    deploy_cmd = [
        'python3', 'scripts/deploy_to_hf.py',
        '--repo-path', args.repo_path,
        '--space-id', args.space_id,
        '--token', hf_token,
        '--branch', args.branch,
        '--create'
    ]
    subprocess.run(deploy_cmd, check=True)

    # Space URL
    space_url = get_hf_space_url(args.space_id)
    print(f"Space URL: {space_url}")
    print("Waiting for Space to be ready...")
    time.sleep(30)

    print("--- Phase 3: API Discovery & Testing ---")
    log_sheet = []

    # Try discovery via Gradio first
    try:
        client = Client(args.space_id)
        api_info = client.view_api(return_format='dict')
        for ep in api_info['endpoints']:
            log_sheet.append({
                "endpoint": f"Gradio: {ep}",
                "status": 200,
                "success": True,
                "details": str(ep),
                "classification": "OK"
            })
    except Exception as e:
        print(f"Gradio discovery skipped: {e}")

    # Root test
    root_res = test_endpoint(space_url)
    log_sheet.append({
        "endpoint": "/",
        "status": root_res['status'],
        "success": root_res['success'],
        "details": str(root_res.get('response', root_res.get('error', ''))),
        "classification": "OK" if root_res['success'] else "FAILURE"
    })

    # Manual scan
    found = scan_for_endpoints(args.repo_path)
    for f in found:
        if isinstance(f['match'], str) and f['match'].startswith('/'):
            t_res = test_endpoint(space_url, f['match'])
            log_sheet.append({
                "endpoint": f['match'],
                "status": t_res['status'],
                "success": t_res['success'],
                "details": str(t_res.get('response', t_res.get('error', ''))),
                "classification": "OK" if t_res['success'] else "FAILURE"
            })

    sheet_path = save_log_sheet(log_sheet, args.repo_path)
    print(f"Results saved to {sheet_path}")

    print("--- Phase 4: Classification & Reporting ---")
    failures = [e for e in log_sheet if e['classification'] == 'FAILURE']
    if failures:
        fail_ctx = json.dumps(failures, indent=2)
        analysis = llm.invoke(f"The following endpoints failed on the HF Space deployment: {fail_ctx}. Classify as CODE_ERROR or TEST_ERROR. If CODE_ERROR, describe the bug.")
        print(f"Analysis: {analysis.content}")

        if "CODE_ERROR" in analysis.content.upper() and github_token and args.github_repo:
            report_cmd = [
                'python3', 'scripts/report_to_jules.py',
                '--repo', args.github_repo,
                '--branch', args.branch,
                '--error-msg', f"Failures:\n{fail_ctx}\n\nAnalysis:\n{analysis.content}",
                '--token', github_token,
                '--space-id', args.space_id
            ]
            subprocess.run(report_cmd, check=False)

if __name__ == "__main__":
    main()
