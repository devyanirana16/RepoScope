FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app
RUN pip install --no-cache-dir 'gunicorn>=23,<27' \
    && useradd --no-create-home --uid 10001 reposcope
COPY recruiter_app.py github_readme.py repo_qa.py ./
COPY web/ web/
USER 10001
EXPOSE 8000
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 60 recruiter_app:application"]
