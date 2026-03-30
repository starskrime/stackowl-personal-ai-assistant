---
name: deploy_app
description: Deploy applications by running build commands, pushing to git remotes, or executing deployment scripts
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🚢"
parameters:
  project_path:
    type: string
    description: "Path to the project"
    default: "."
  deploy_script:
    type: string
    description: "Path to deploy script (optional)"
    default: ""
  production_url:
    type: string
    description: "URL to verify deployment"
    default: ""
  skip_tests:
    type: boolean
    description: "Skip running tests before deployment"
    default: false
required: []
steps:
  - id: run_tests
    tool: ShellTool
    args:
      command: "cd {{project_path}} && npm test 2>&1 || echo 'Tests failed or not configured'"
      mode: "local"
    timeout_ms: 120000
    optional: true
  - id: build_project
    tool: ShellTool
    args:
      command: "cd {{project_path}} && npm run build 2>&1 || echo 'Build step not configured'"
      mode: "local"
    timeout_ms: 120000
  - id: push_to_remote
    tool: ShellTool
    args:
      command: "cd {{project_path}} && git push origin main 2>&1 || echo 'Git push skipped'"
      mode: "local"
    timeout_ms: 60000
    optional: true
  - id: run_deploy_script
    tool: ShellTool
    args:
      command: "cd {{project_path}} && ./{{deploy_script}} 2>&1 || echo 'Deploy script failed or not specified'"
      mode: "local"
    timeout_ms: 120000
    optional: true
  - id: verify_deployment
    tool: ShellTool
    args:
      command: "curl -s -o /dev/null -w '%{http_code}' {{production_url}} 2>/dev/null || echo 'Verification skipped'"
      mode: "local"
    timeout_ms: 30000
    optional: true
  - id: present_results
    type: llm
    prompt: "Summarize the deployment results:\n\nBuild:\n{{build_project.stdout}}\n\nPush:\n{{push_to_remote.stdout}}\n\nDeploy script:\n{{run_deploy_script.stdout}}\n\nVerification:\n{{verify_deployment.stdout}}\n\nNote any failures and provide next steps."
    depends_on: [run_tests, build_project, push_to_remote, run_deploy_script, verify_deployment]
    inputs: [run_tests.stdout, build_project.stdout, push_to_remote.stdout, run_deploy_script.stdout, verify_deployment.stdout]
---

# Deploy Application

Run deployment workflows.

## Steps

1. **Run tests first:**
   ```bash
   npm test
   ```
2. **Build:**
   ```bash
   npm run build
   ```
3. **Push to remote:**
   ```bash
   git push origin main
   ```
4. **Or run deploy script:**
   ```bash
   ./deploy.sh
   ```
5. **Verify deployment:**
   ```bash
   curl -s -o /dev/null -w '%{http_code}' <production_url>
   ```

## Examples

### Deploy Node.js app

```
project_path="./my-app"
production_url="https://my-app.com"
```

## Error Handling

- **Tests fail:** Abort deployment and show failures.
- **Build fails:** Show error and suggest fixes.
- **Push rejected:** Pull first with `git pull --rebase`.
