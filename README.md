# QA Test Agent

An AI-powered automated testing solution that analyzes requirement documentation and generates/executes comprehensive test cases using Claude AI and Playwright.

## 🚀 Features

- **AI-Powered Requirement Analysis**: Automatically extract testable requirements from documentation
- **Smart Test Generation**: Generate complete Playwright test scripts from requirements
- **Automated Test Execution**: Execute tests against web applications with evidence capture
- **Comprehensive Reporting**: AI-generated test reports with insights and recommendations
- **Cloud Storage Integration**: Store test evidence in Azure Blob Storage
- **User-Friendly Interface**: Streamlit-based web interface for easy interaction

## 🛠️ Technology Stack

- **LLM**: Claude 3.5 Sonnet via Anthropic API
- **UI Automation**: Playwright (Python)
- **Frontend**: Streamlit
- **Document Processing**: Unstructured library
- **Cloud Storage**: Azure Blob Storage
- **Containerization**: Docker

## 📋 Requirements

- Python 3.8+
- Claude API key
- (Optional) Azure Storage account

### ⚠️ Current Status & Limitations (Beta)
- **State Management**: Test case runs are currently held in-memory via Streamlit. If the application restarts, or the browser is refreshed mid-run, execution state is lost. 
- **Concurrency**: The UI is blocking during execution. Running heavy tests (or multiple concurrent users running browsers via Playwright) will cause significant memory/CPU spikes.
- **Security**: The Streamlit interface is entirely exposed by default. Authentication is needed for public deployments to prevent arbitrary automated requests (SSRF mitigation) and Claude API billing abuses.

## 🚀 Quick Start

### 1. Clone and Setup

```bash
# Navigate to the project directory
cd qa-test-agent

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

### 2. Configuration

Create a `.env` file with your configuration:

```env
# Required
CLAUDE_API_KEY=your_anthropic_api_key_here

# Optional - for Azure storage
AZURE_STORAGE_CONNECTION_STRING=your_azure_connection_string
AZURE_CONTAINER_NAME=test-evidence

# Optional - Playwright settings
PLAYWRIGHT_TIMEOUT=30000
PLAYWRIGHT_HEADLESS=false
```

### 3. Run the Application

```bash
streamlit run app.py
```

The application will be available at `http://localhost:8501`

## 📖 Usage

### 1. Upload Requirements
- Upload requirement documentation (PDF, DOCX, TXT, MD)
- Or use the "Sample Requirements" button for testing

### 2. Generate Test Cases
- Click "Generate Test Cases" to create Playwright scripts
- Review and edit generated test cases if needed

### 3. Execute Tests
- Configure application URL and browser settings
- Click "Execute All Tests" to run the test suite
- View execution results and captured evidence

### 4. Generate Reports
- Click "Generate Report" for comprehensive AI analysis
- Download HTML reports or view online (if Azure configured)

## 🏗️ Architecture

```
qa-test-agent/
├── app.py                 # Streamlit frontend
├── config.py             # Configuration management
├── models.py             # Data models
├── llm_processor.py      # Claude API integration
├── playwright_executor.py # Test execution engine
├── azure_storage.py      # Evidence storage
├── requirements.txt      # Python dependencies
├── Dockerfile           # Container configuration
├── .env.example         # Configuration template
└── test_components.py   # Component tests
```

## 🔧 Key Components

### LLM Processor
- Analyzes requirement documents using Claude API
- Generates testable requirements with acceptance criteria
- Creates complete Playwright test scripts
- Produces comprehensive test reports

### Playwright Executor
- Executes generated test cases in real browsers
- Captures screenshots at each test step
- Handles authentication and complex interactions
- Provides detailed execution metrics

### Storage Manager
- Stores test evidence (screenshots, logs, reports)
- Supports both Azure Blob Storage and local storage
- Organizes evidence by execution ID and timestamp

## 🧪 Testing

Run component tests to verify installation:

```bash
python test_components.py
```

## 🐳 Docker Deployment

### Build and Run

```bash
# Build the image
docker build -t qa-test-agent .

# Run the container
docker run -p 8501:8501 \
  -e CLAUDE_API_KEY=your_key_here \
  qa-test-agent
```

### Azure Deployment

```bash
# Push to Azure Container Registry
az acr login --name yourregistry
docker tag qa-test-agent yourregistry.azurecr.io/qa-test-agent
docker push yourregistry.azurecr.io/qa-test-agent

# Deploy to Azure App Service
az webapp create \
  --name qa-test-agent-app \
  --plan your-plan \
  --resource-group your-rg \
  --deployment-container-image-name yourregistry.azurecr.io/qa-test-agent
```

## 🔒 Security & Deployment Best Practices

### Pre-Deployment Checklist
Before deploying this agent publicly, ensure the following constraints are handled:
1. **Authentication**: The application acts as a browser-automation endpoint. You MUST place it behind a Cloudflare Zero Trust proxy, corporate VPN, or implement basic `st_auth` login on the `app.py` wrapper to secure it.
2. **Resource Limits**: Headless browsers are memory hogs. Set a strict memory limit on your container (absolute minimum **2GB RAM, 1 vCPU**) to prevent `OOM` (Out-of-Memory) Container crashes during tests. 
3. **Network Isolation**: Ensure the host server/VPC restricts the running container from accessing internal/metadata IPs (like 169.254.169.254) to eliminate SSRF attack vectors from malicious requirements uploads.
4. **Secret Management**: Never commit `.env` files to version control. Prefer cloud-native secrets managers for `CLAUDE_API_KEY` and Azure connection strings.

## 📊 Sample Workflow

1. **Upload** requirements document
2. **Analyze** with Claude AI to extract testable requirements
3. **Generate** Playwright test scripts for each requirement
4. **Execute** tests against your application
5. **Capture** screenshots and logs as evidence
6. **Generate** AI-powered test report with insights
7. **Store** all evidence in cloud storage

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests to ensure everything works
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Support

For issues and questions:
- Check the component tests first
- Review the logs in the application
- Ensure all dependencies are properly installed
- Verify your Claude API key is valid and has sufficient credits

## 🎯 Future Enhancements

- Support for more document formats
- Integration with test management tools
- Advanced test scheduling and CI/CD integration
- Performance testing capabilities
- Visual regression testing
- API testing support