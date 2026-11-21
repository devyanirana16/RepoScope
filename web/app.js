const $ = (id) => document.getElementById(id);
const form = $("search-form");
const account = $("account");
const authStatus = $("auth-status");
const browseRepositories = $("browse-repositories");
const repositoryPicker = $("repository-picker");
const repositoryOptions = $("repository-options");
const repositoryStatus = $("repository-status");
let login = null;
let signInAvailable = true;
let signInPending = false;
let timer;
let searchVersion = 0;
let repositoryVersion = 0;

async function api(path, fields) {
  const options = fields === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  };
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error);
  return data;
}

function accountState() {
  account.textContent = login ? "Sign out" : signInPending ? "Cancel sign-in" : "Sign in with GitHub";
  $("access-state").textContent = login ? `Signed in as @${login}` : "Public access";
  $("access-detail").textContent = login
    ? "Search repositories your GitHub account and the app can access."
    : "Search public repositories without signing in.";
  $("auth-instructions").hidden = !signInPending;
  browseRepositories.hidden = !login;
  if (!login) {
    closeRepositoryPicker();
    browseRepositories.disabled = false;
  }
}

function closeRepositoryPicker() {
  repositoryVersion++;
  repositoryPicker.hidden = true;
  repositoryOptions.hidden = true;
  repositoryOptions.replaceChildren();
  repositoryStatus.textContent = "";
  browseRepositories.setAttribute("aria-expanded", "false");
}

browseRepositories.addEventListener("click", async () => {
  if (!repositoryPicker.hidden) {
    closeRepositoryPicker();
    return;
  }
  const version = ++repositoryVersion;
  repositoryPicker.hidden = false;
  browseRepositories.setAttribute("aria-expanded", "true");
  browseRepositories.disabled = true;
  repositoryStatus.textContent = "Loading repositories…";
  try {
    const { repositories } = await api("/api/repositories", {});
    if (version !== repositoryVersion) return;
    if (!repositories.length) {
      repositoryStatus.textContent = "No repositories are available through this GitHub App.";
      return;
    }
    repositoryOptions.replaceChildren(new Option("Choose a repository", ""));
    for (const repository of repositories) repositoryOptions.add(new Option(repository, repository));
    repositoryOptions.hidden = false;
    repositoryStatus.textContent = "";
    repositoryOptions.focus();
  } catch (error) {
    if (version === repositoryVersion) repositoryStatus.textContent = error.message;
  } finally {
    if (version === repositoryVersion) browseRepositories.disabled = false;
  }
});

repositoryOptions.addEventListener("change", () => {
  if (!repositoryOptions.value) return;
  $("repository").value = repositoryOptions.value;
  closeRepositoryPicker();
  $("query").focus();
});

function message(text) {
  $("result-count").textContent = "";
  const box = document.createElement("div");
  box.className = "panel empty";
  const icon = document.createElement("span");
  icon.className = "search-icon";
  icon.setAttribute("aria-hidden", "true");
  const line = document.createElement("p");
  line.textContent = text;
  box.append(icon, line);
  $("results").replaceChildren(box);
}

function showResults(results) {
  if (!results.length) {
    message("No matching chunks. Try different search terms.");
    return;
  }
  $("result-count").textContent = `${results.length} ${results.length === 1 ? "match" : "matches"}`;
  $("results").replaceChildren(...results.map(({ citation, excerpt }) => {
    const article = document.createElement("article");
    article.className = "panel result";
    const text = document.createElement("p");
    text.textContent = excerpt;
    const link = document.createElement("a");
    const url = new URL(citation);
    if (url.origin === "https://github.com") link.href = url.href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = url.pathname.split("/").slice(4).join("/") + url.hash + " on GitHub";
    article.append(text, link);
    return article;
  }));
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const version = ++searchVersion;
  const submit = form.querySelector("[type=submit]");
  submit.disabled = true;
  message("Searching the README…");
  try {
    const { results } = await api("/api/search", {
      repository: $("repository").value,
      query: $("query").value,
      signedIn: Boolean(login),
    });
    if (version === searchVersion) showResults(results);
  } catch (error) {
    if (version === searchVersion) message(error.message);
  } finally {
    if (version === searchVersion) submit.disabled = false;
  }
});

document.querySelectorAll("[data-repository]").forEach((button) => {
  button.addEventListener("click", () => {
    $("repository").value = button.dataset.repository;
    $("query").value = button.dataset.query;
    form.requestSubmit();
  });
});

function schedulePoll(seconds) {
  timer = setTimeout(async () => {
    // Serialize an in-flight poll with sign-out so a late response cannot restore a session.
    account.disabled = true;
    try {
      const data = await api("/api/sign-in/poll", {});
      if (data.login) {
        login = data.login;
        signInPending = false;
        authStatus.textContent = "";
        accountState();
      } else {
        schedulePoll(data.retryAfter);
      }
    } catch (error) {
      signInPending = false;
      authStatus.textContent = error.message;
      accountState();
    } finally {
      account.disabled = false;
    }
  }, seconds * 1000);
}

account.addEventListener("click", async () => {
  account.disabled = true;
  authStatus.textContent = "";
  try {
    if (login || signInPending) {
      clearTimeout(timer);
      await api("/api/sign-out", {});
      if (login) {
        searchVersion++;
        $("repository").value = "";
        form.querySelector("[type=submit]").disabled = false;
        message("Choose a repository and enter search terms.");
      }
      login = null;
      signInPending = false;
    } else if (!signInAvailable) {
      authStatus.textContent = "Set GITHUB_APP_CLIENT_ID before starting RepoScope to enable sign-in.";
    } else {
      const data = await api("/api/sign-in", {});
      $("device-code").textContent = data.code;
      signInPending = true;
      schedulePoll(data.retryAfter);
    }
  } catch (error) {
    authStatus.textContent = error.message;
  } finally {
    accountState();
    account.disabled = false;
  }
});

account.disabled = true;
api("/api/session").then((data) => {
  login = data.login;
  signInAvailable = data.signInAvailable;
  accountState();
  account.hidden = Boolean(data.publicDemo);
}).catch((error) => {
  authStatus.textContent = error.message;
}).finally(() => {
  account.disabled = false;
});

const demoButton = $("run-demo");
demoButton.addEventListener("click", async () => {
  const status = $("demo-status");
  const output = $("demo-output");
  demoButton.disabled = true;
  output.hidden = true;
  status.textContent = "Running the permission check…";
  status.hidden = false;
  try {
    const demo = await api("/api/demo");
    $("demo-question").textContent = demo.question;
    $("demo-before").replaceChildren(...demo.before.map(({ excerpt, citation }) => {
      const item = document.createElement("li");
      const source = document.createElement("code");
      source.textContent = citation;
      item.append(excerpt, source);
      return item;
    }));
    $("demo-after").textContent = demo.after;
    status.hidden = true;
    output.hidden = false;
  } catch (error) {
    status.textContent = error.message;
  } finally {
    demoButton.disabled = false;
  }
});
