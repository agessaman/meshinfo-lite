# Use the official Python slim image as the base
FROM python:3.10-slim

LABEL org.opencontainers.image.source https://github.com/kevinelliott/meshinfo

# Create a user for the container
RUN mkdir /app
RUN adduser --disabled-password --gecos '' appuser && chown -R appuser /app
USER appuser

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Add a HEALTHCHECK instruction
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 CMD python -c "import socket; s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); result = s.connect_ex(('localhost', 8000)); s.close(); exit(result)"

# Copy the project code
COPY . .

# Set the command to run the application
CMD ["python", "app.py"]
