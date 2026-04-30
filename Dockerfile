# Use the official Microsoft Playwright image as it contains all the browser binaries
# necessary for headless visual testing and Vite execution.
FROM mcr.microsoft.com/playwright:v1.43.0-jammy

# Set up the working directory inside the container
WORKDIR /app

# Install basic OS utilities the agent might need (e.g., for file manipulation or networking tests)
RUN apt-get update && apt-get install -y \
    curl \
    git \
    rsync \
    && rm -rf /var/lib/apt/lists/*

# Install global node tools that the agent will use for project generation and type checking
RUN npm install -g typescript ts-node vite vitest

# Set environment variables for Playwright (headless)
ENV CI=true

# Keep the container running infinitely so the supervisor can attach to it and run commands via 'docker exec'
CMD ["tail", "-f", "/dev/null"]
