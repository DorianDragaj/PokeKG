# Runtime for every Python step of the pipeline (extract, transform, load,
# reason, entail, embed, serve). Pairs with the `app` service in
# docker-compose.yml so a fresh checkout needs Docker and nothing else.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/app

WORKDIR /app

# CPU-only torch, installed before requirements.txt so that the `torch>=2.2`
# line there is already satisfied. The default PyPI wheel pulls ~2.5 GB of CUDA
# runtime libraries this project never uses: training runs on CPU in ~7 min.
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.2"

COPY requirements.txt .
RUN pip install -r requirements.txt

# The container runs as the host user's UID so that generated artefacts in the
# bind-mounted tree stay owned by them. That UID has no passwd entry, so give
# it a world-writable HOME for the caches PyKEEN and Matplotlib create.
RUN mkdir -p /home/app && chmod 777 /home/app

CMD ["python", "-m", "pokekg.store"]
