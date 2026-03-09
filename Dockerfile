# Use a Python slim image
FROM python:3.11-slim as builder

WORKDIR /app
COPY requirements.txt .

# Install build dependencies for unstructured/Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages to a virtual environment to copy over later
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Final stage
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

WORKDIR /app

# Copy the Python environment from the builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install only chromium (the microsoft image has the OS deps already)
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy application code
COPY . .

# Create runtime directories
RUN mkdir -p screenshots test_evidence

# Force headless mode in production (no display available on server)
ENV PLAYWRIGHT_HEADLESS=true

# Streamlit — disable XSRF protection for Railway's proxy and listen on all interfaces
ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=false

# Expose Streamlit port
EXPOSE 8501

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health

# Run the application
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true", "--server.enableCORS=false", "--server.enableXsrfProtection=false"]
