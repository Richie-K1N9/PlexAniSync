# Building and publishing the Docker image

This guide covers building the PlexAniSync container locally, pushing it to a registry, and the GitHub Actions workflow that automates the same flow.

The default image name used throughout the docs is `ghcr.io/richie-k1n9/plexanisync:latest`. Substitute your own owner/name everywhere if you're publishing under a different account.

---

## 1. Build locally for development

From the repository root:

```bash
docker build \
  --file Docker/PlexAniSync/Dockerfile \
  --tag plexanisync:dev \
  .
```

That builds for your host's architecture only (faster). To run it:

```bash
docker run --rm \
  -e PLEX_URL=http://192.168.1.10:32400 \
  -e PLEX_TOKEN=xxxxxxxx \
  -e ANI_USERNAME=yourname \
  -e ANI_TOKEN=yyyyyyyy \
  -e INTERVAL=0 \
  plexanisync:dev
```

`INTERVAL=0` makes the container run one sync and exit, which is what you want for an iteration loop. Drop the flag (or set a positive value) to run the normal cron-like loop.

---

## 2. Build using docker compose

The included [docker-compose.yml](docker-compose.yml) defaults to pulling the published image. To build from the local source instead:

1. Open `docker-compose.yml`.
2. Comment out the `image:` line.
3. Uncomment the `build:` block.
4. Run:

```bash
cp .env.example .env
# edit .env, fill in tokens
docker compose build
docker compose up -d
docker compose logs -f
```

---

## 3. Build a multi-arch image (amd64 + arm64 + armv7)

The published image supports three architectures so it can run on x86 servers, Apple Silicon, and Raspberry Pi 3/4. Local multi-arch builds need [Docker Buildx](https://docs.docker.com/build/install-buildx/) and QEMU emulation.

```bash
# One-time setup
docker buildx create --use --name multi
docker run --privileged --rm tonistiigi/binfmt --install all

# Build and push in one step (cannot --load multi-platform builds, only push)
docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7 \
  --file Docker/PlexAniSync/Dockerfile \
  --tag ghcr.io/richie-k1n9/plexanisync:latest \
  --tag ghcr.io/richie-k1n9/plexanisync:1.0.0 \
  --push \
  .
```

You must `docker login ghcr.io` before pushing — see step 4.

---

## 4. Push to GitHub Container Registry (ghcr.io)

GHCR is free for public repos, has no rate limits for public pulls, and is tied to your GitHub account.

### One-time setup

1. Create a [Personal Access Token (classic)](https://github.com/settings/tokens/new) with the `write:packages` and `read:packages` scopes.
2. Log in:

```bash
echo $YOUR_PAT | docker login ghcr.io -u Richie-K1N9 --password-stdin
```

### Push

```bash
docker tag plexanisync:dev ghcr.io/richie-k1n9/plexanisync:latest
docker push ghcr.io/richie-k1n9/plexanisync:latest
```

After the first push, find your image at `https://github.com/Richie-K1N9?tab=packages`. By default it'll be private — open the package's settings and set visibility to public if you want others to pull without auth.

---

## 5. Push to Docker Hub instead (optional)

If you'd rather host on Docker Hub:

```bash
docker login                                                 # docker.io
docker tag plexanisync:dev richie-k1n9/plexanisync:latest
docker push richie-k1n9/plexanisync:latest
```

Then update `docker-compose.yml` and the Docker README to point at `richie-k1n9/plexanisync:latest` (no `ghcr.io/` prefix).

---

## 6. Automated builds via GitHub Actions

The repo includes a working CI workflow at [.github/workflows/CI.yml](.github/workflows/CI.yml). On push to `master` or on a `v*.*.*` tag, it:

1. Runs lint + tests.
2. Builds the `Docker/PlexAniSync/Dockerfile` for all three architectures.
3. Pushes to `ghcr.io/${{ github.repository_owner }}/plexanisync` — which becomes `ghcr.io/richie-k1n9/plexanisync` once the repo is under your account.
4. Builds and pushes the Tautulli-combined image too.

### What you need to enable it

| Step | Where |
|---|---|
| Push the repo to GitHub under your account | `git remote set-url origin https://github.com/Richie-K1N9/PlexAniSync.git && git push -u origin master` |
| (Optional) Add Docker Hub credentials so it dual-publishes | Repo Settings → Secrets and variables → Actions → add `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` |
| Make sure Actions has package-write permission | Repo Settings → Actions → General → Workflow permissions → "Read and write permissions" |

GHCR credentials are automatic — the workflow uses `secrets.GITHUB_TOKEN`, which GitHub provides for free.

### Tagged releases

To cut a release:

```bash
git tag -a v1.0.0 -m "Initial cache + webhook release"
git push origin v1.0.0
```

The workflow will publish both `:1.0.0` and `:1.0` tags in addition to whatever was already at `:latest`.

---

## 7. Pruning old images

`.github/workflows/prune-docker-images.yml` runs on a schedule to delete untagged image layers from ghcr.io. Nothing to configure — but if you fork into a private repo, make sure the workflow has permission to delete packages (Settings → Actions → General → Workflow permissions).

---

## Troubleshooting

**"denied: installation not allowed to Create organization package"** when pushing to GHCR
→ Your PAT doesn't have `write:packages`. Regenerate with the scope checked.

**Buildx fails with `exec format error` on arm64**
→ QEMU isn't installed. Re-run `docker run --privileged --rm tonistiigi/binfmt --install all`.

**Image builds but the container exits immediately**
→ Check `docker compose logs plexanisync`. Common causes: missing required env vars (`PLEX_TOKEN`, `ANI_TOKEN`), or `INTERVAL=0` which runs once and exits by design.

**Container can't reach Plex at `127.0.0.1:32400`**
→ Inside a container, `127.0.0.1` is the container itself, not the host. Use the host's LAN IP, or add `network_mode: host` to the compose service (Linux only). On Docker Desktop (Mac/Windows) use `http://host.docker.internal:32400`.

**Cache file appears in the repo root after running locally**
→ Expected — `plexanisync_cache.db` is created by `python PlexAniSync.py`. It's in `.gitignore`. Delete with `rm plexanisync_cache.db` if you want a clean run.
