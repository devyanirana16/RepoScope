# RepoScope

RepoScope searches a GitHub repository’s README and links each result to the exact commit and line it came from. It also demonstrates how a repository search can honor an access change even when the indexed content stays in place.

**[Open the live demo on Azure Container Apps](https://reposcope.proudsand-357fc3d3.centralindia.azurecontainerapps.io/)**

## Try the demo

1. Open the live app and click **Run demo**. It asks “What happened with suspicious payments?” using fictional data. Before revocation, the sample user can see evidence from the fictional `fraud-engine` repository. After revocation, the same index is queried again and returns no answer or citations. An explicit request for the revoked repository is denied.
2. Try a live search with `octocat/Hello-World` as the repository and `hello world` as the query. Open a result’s citation to see the original README at the commit RepoScope searched.

Azure can scale the app to zero replicas when idle, so the first visit after a quiet period may take longer. The hosted demo can search public repositories without sign-in. GitHub App sign-in is available through the local development server.

## How it works

### Live README search

1. The user enters `OWNER/REPO` or a GitHub repository URL and a keyword query.
2. The server fetches repository metadata through the GitHub API. Signed-out requests require a public repository.
3. The server finds the latest commit on the repository’s default branch and fetches the README at that SHA. Using the same SHA for the fetch and the citation keeps the link stable when the branch advances.
4. RepoScope splits Markdown into chunks, filters eligible content by repository access, and ranks matches by keyword overlap. Rarer matching words in the eligible chunks receive more weight.
5. The UI displays the best matching line from each result with a commit-pinned GitHub link.

Live searches fetch one repository’s README on demand. They use lexical ranking and extractive excerpts. An empty result means the query matched no eligible chunks. Anonymous GitHub API limits and repositories with missing READMEs can affect live searches.

### Permission-aware retrieval

The fictional dataset adds issues and release notes to the README examples. The index stores repository content, and the access map controls what each user can retrieve. RepoScope checks that map before calculating ranking weights or selecting excerpts. In the revocation scenario, removing the repository from the map changes which chunks can contribute to the answer without rebuilding the index. If the user names a repository they cannot access, the core raises a permission error.

## Project layout

| Path | Purpose |
| --- | --- |
| [`recruiter_app.py`](recruiter_app.py) | Public WSGI server, live search API, permission demo, and health check |
| [`web/`](web/) | Shared browser interface in HTML, CSS, and JavaScript |
| [`github_readme.py`](github_readme.py) | GitHub API client, commit-pinned README search, device sign-in, and CLI |
| [`repo_qa.py`](repo_qa.py) | Chunking, access filtering, ranking, and extractive answers over sample data |
| [`web_app.py`](web_app.py) | Loopback development server with optional GitHub App sign-in |
| [`test_repo_qa.py`](test_repo_qa.py) and [`eval_repo_qa.py`](eval_repo_qa.py) | Revocation regression test and evaluation over fictional data |
| [`Dockerfile`](Dockerfile) | Python 3.12 image that starts the public WSGI server with Gunicorn as a non-root user |
| [`deploy-azure.sh`](deploy-azure.sh) and [`azure-container-app.bicep`](azure-container-app.bicep) | Registry and image build workflow, plus Azure Container Apps infrastructure |

## Run locally

Use Python 3.12 to match the container. The local development commands use the Python standard library.

```bash
git clone https://github.com/devyanirana16/RepoScope.git
cd RepoScope
python3 -B recruiter_app.py
```

Open <http://127.0.0.1:8765>. You can also run a live search or the sample answer retrieval from the terminal:

```bash
python3 -B github_readme.py octocat/Hello-World "hello world"
python3 -B repo_qa.py "What happened with suspicious payments?" \
  --user arjun --repo fraud-engine --answer
```

### Local GitHub App sign-in

Enable Device Flow on a GitHub App, give it read-only Contents access, and install it on the repositories to search. Copy the app’s client ID and start the local development server:

```bash
GITHUB_APP_CLIENT_ID="<github-app-client-id>" python3 -B web_app.py
```

Open <http://127.0.0.1:8765> and follow the GitHub sign-in prompts. The server keeps tokens in memory and uses a loopback-only session cookie. Signed-in search uses the current user’s GitHub App token. GitHub controls which repositories that token can access.

### Run the container locally

```bash
docker build -t reposcope .
docker run --rm -p 127.0.0.1:8000:8000 reposcope
```

Open <http://127.0.0.1:8000> or check the process with `curl -fsS http://127.0.0.1:8000/healthz`.

## Public HTTP API

| Method | Path | Response |
| --- | --- | --- |
| GET | `/` | Browser interface |
| GET | `/healthz` | Plain-text `OK` |
| GET | `/api/session` | Hosted demo mode and sign-in availability |
| GET | `/api/demo` | Sample question, cited evidence before revocation, and the outcome after revocation |
| POST | `/api/search` | JSON with a `results` array of `citation` and `excerpt` pairs |

```bash
curl -fsS http://127.0.0.1:8765/api/search \
  -H 'Content-Type: application/json' \
  --data '{"repository":"octocat/Hello-World","query":"hello world"}'
```

## Deploy to Azure Container Apps

Install the Azure CLI and sign in to a subscription where you can register providers and create resources. Choose a region allowed by your subscription and pick a globally unique Azure Container Registry name. Reuse that registry name and resource group on retries.

```bash
az login
az account show --query '{subscription:name,id:id}' -o table

AZURE_LOCATION=centralindia \
AZURE_RESOURCE_GROUP=reposcope-centralindia-rg \
AZURE_ACR_NAME="<your-unique-registry-name>" \
bash deploy-azure.sh
```

The script registers the required providers, creates a Basic registry, and builds and pushes the image. The Bicep template provisions Log Analytics, a Container Apps environment, and the app. It sets external HTTPS ingress to target port `8000`, uses `/healthz` for readiness and liveness probes, and allows zero to one replicas. The script prints the public HTTPS URL.

### Build locally when ACR Tasks are unavailable

If Azure returns `TasksOperationsNotAllowed`, start Docker Desktop or another local Docker engine and verify that `docker info` can reach it. Rerun the deployment with the same registry and resource group:

```bash
AZURE_LOCATION=centralindia \
AZURE_RESOURCE_GROUP=reposcope-centralindia-rg \
AZURE_ACR_NAME="<your-existing-registry-name>" \
AZURE_BUILD_LOCAL=1 \
bash deploy-azure.sh
```

The local build targets Linux AMD64, pushes the image to Azure Container Registry, and deploys the app through the same template. Azure runs the deployed container after the build machine disconnects or shuts down.

| Variable | Default or behavior |
| --- | --- |
| `AZURE_SUBSCRIPTION_ID` | Active Azure CLI subscription |
| `AZURE_LOCATION` | `eastus` |
| `AZURE_RESOURCE_GROUP` | `reposcope-rg` |
| `AZURE_APP_NAME` | `reposcope` |
| `AZURE_ENVIRONMENT_NAME` | `reposcope-env` |
| `AZURE_ACR_NAME` | Randomly generated registry name when omitted |
| `AZURE_BUILD_LOCAL` | Set to `1` to build and push using local Docker |

### Verify the deployment

Run `curl -fsS https://<your-app-host>/healthz` and check for `OK`. Open the same host in a browser, run the permission demo, and try the public README search.

If Azure returns `RequestDisallowedByAzure` for the region, check **Azure Policy → Assignments → Allowed resource deployment regions** for your subscription. Set `AZURE_LOCATION` to an allowed region and use a new resource group if the original group is already in another region. If Docker Desktop is installed on a Mac but the shell cannot find `docker`, open a fresh terminal after setup or add the Docker CLI directory to that terminal’s `PATH`.

## Tests and evaluation

```bash
python3 -B -m unittest -v
python3 -B eval_repo_qa.py
```

The unit test checks that revocation excludes a repository from later searches. The evaluation runs 50 fictional questions across direct lookup, cross-document evidence, conflicting sources, stale information, and refusal. It reports retrieval and answer checks, counts grounded excerpts and authorized citations, and runs an explicit role-change scenario against an unchanged index.

The documented run passed 50 of 50 retrieval checks and 50 of 50 answer checks, with 40 grounded excerpts, 40 authorized citations, and zero permission leaks across the fixture cases. All three role-change checks passed. These results describe the included fictional dataset.
