# Agente de cotação AutoSeguro (o serviço de cotação tem a sua própria imagem em quote-service/)
FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.12.5

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

# dependências primeiro, para o cache de camadas sobreviver a mudanças no código
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["autoseguro"]
CMD ["chat"]
