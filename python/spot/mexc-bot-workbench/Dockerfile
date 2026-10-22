FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 WORKBENCH_DATA_DIR=/data/controlled PORT=8080
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY paper_runner.py monitor.py mexc_bot.py ./
COPY controlled_paper.py technical_market.py workbench.py ./
COPY execution_readiness.py ./
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/health', timeout=4)"
CMD ["python", "-u", "workbench.py", "run"]
