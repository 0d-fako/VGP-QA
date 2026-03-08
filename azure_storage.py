import os
import logging
from datetime import datetime
from typing import List, Optional
from azure.storage.blob import BlobServiceClient, ContainerClient
from config import config

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AzureStorageManager:
    """Manage Azure Blob Storage for test evidence"""
    
    def __init__(self):
        """Initialize Azure storage connection"""
        self.connection_string = config.AZURE_STORAGE_CONNECTION_STRING
        self.container_name = config.AZURE_CONTAINER_NAME
        self.blob_service_client = None
        self.container_client = None
        
        if self.connection_string:
            self._initialize_clients()
    
    def _initialize_clients(self):
        """Initialize Azure Blob Storage clients"""
        try:
            self.blob_service_client = BlobServiceClient.from_connection_string(
                self.connection_string
            )
            self.container_client = self.blob_service_client.get_container_client(
                self.container_name
            )
            
            # Create container if it doesn't exist
            try:
                self.container_client.create_container()
                logger.info(f"Container '{self.container_name}' created or already exists")
            except Exception as e:
                if "ContainerAlreadyExists" not in str(e):
                    logger.warning(f"Container creation warning: {str(e)}")
                    
        except Exception as e:
            logger.error(f"Failed to initialize Azure Storage clients: {str(e)}")
            self.blob_service_client = None
            self.container_client = None
    
    def upload_screenshot(self, local_path: str, execution_id: str, 
                         state: str = "final") -> Optional[str]:
        """
        Upload a screenshot to Azure Blob Storage
        
        Args:
            local_path (str): Local path to the screenshot
            execution_id (str): Test execution ID
            state (str): State identifier (initial, final, failure, etc.)
            
        Returns:
            Optional[str]: URL of the uploaded blob or None if failed
        """
        if not self.container_client:
            logger.warning("Azure Storage not configured, skipping upload")
            return None
            
        try:
            # Generate blob name
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.basename(local_path)
            blob_name = f"screenshots/{execution_id}/{state}_{timestamp}_{filename}"
            
            # Upload file
            with open(local_path, "rb") as data:
                blob_client = self.container_client.upload_blob(
                    name=blob_name,
                    data=data,
                    overwrite=True
                )
            
            # Generate URL
            blob_url = f"https://{self.blob_service_client.account_name}.blob.core.windows.net/{self.container_name}/{blob_name}"
            logger.info(f"Screenshot uploaded: {blob_name}")
            return blob_url
            
        except Exception as e:
            logger.error(f"Failed to upload screenshot {local_path}: {str(e)}")
            return None
    
    def upload_test_report(self, html_content: str, report_id: str) -> Optional[str]:
        """
        Upload test report HTML to Azure Blob Storage
        
        Args:
            html_content (str): HTML content of the report
            report_id (str): Report ID
            
        Returns:
            Optional[str]: URL of the uploaded report or None if failed
        """
        if not self.container_client:
            logger.warning("Azure Storage not configured, skipping upload")
            return None
            
        try:
            # Generate blob name
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            blob_name = f"reports/{report_id}_{timestamp}.html"
            
            # Upload content
            blob_client = self.container_client.upload_blob(
                name=blob_name,
                data=html_content.encode('utf-8'),
                overwrite=True
            )
            
            # Generate URL
            blob_url = f"https://{self.blob_service_client.account_name}.blob.core.windows.net/{self.container_name}/{blob_name}"
            logger.info(f"Test report uploaded: {blob_name}")
            return blob_url
            
        except Exception as e:
            logger.error(f"Failed to upload test report: {str(e)}")
            return None
    
    def upload_execution_evidence(self, execution) -> dict:
        """
        Upload all evidence from a test execution
        
        Args:
            execution: Test execution object with screenshots and logs
            
        Returns:
            dict: Mapping of local paths to blob URLs
        """
        evidence_urls = {}
        
        if not self.container_client:
            logger.warning("Azure Storage not configured")
            return evidence_urls
        
        # Upload screenshots
        for screenshot_path in execution.screenshots:
            if os.path.exists(screenshot_path):
                # Extract state from filename
                filename = os.path.basename(screenshot_path)
                state = "unknown"
                if "initial" in filename:
                    state = "initial"
                elif "final" in filename:
                    state = "final"
                elif "failure" in filename:
                    state = "failure"
                
                url = self.upload_screenshot(screenshot_path, execution.id, state)
                if url:
                    evidence_urls[screenshot_path] = url
        
        # Upload logs as text file
        if execution.logs:
            try:
                log_content = "\n".join(execution.logs)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                blob_name = f"logs/{execution.id}_{timestamp}.log"
                
                blob_client = self.container_client.upload_blob(
                    name=blob_name,
                    data=log_content.encode('utf-8'),
                    overwrite=True
                )
                
                log_url = f"https://{self.blob_service_client.account_name}.blob.core.windows.net/{self.container_name}/{blob_name}"
                evidence_urls["logs"] = log_url
                
            except Exception as e:
                logger.error(f"Failed to upload logs: {str(e)}")
        
        return evidence_urls
    
    def get_evidence_urls(self, execution_ids: List[str]) -> dict:
        """
        Get URLs for all evidence related to execution IDs
        
        Args:
            execution_ids (List[str]): List of execution IDs
            
        Returns:
            dict: Mapping of execution IDs to evidence URLs
        """
        if not self.container_client:
            return {}
        
        evidence_map = {}

        try:
            for execution_id in execution_ids:
                execution_evidence = {
                    "screenshots": [],
                    "logs": [],
                    "reports": []
                }
                
                # Fetch screenshots for this execution
                screenshot_blobs = self.container_client.list_blobs(name_starts_with=f"screenshots/{execution_id}")
                for blob in screenshot_blobs:
                    blob_url = f"https://{self.blob_service_client.account_name}.blob.core.windows.net/{self.container_name}/{blob.name}"
                    execution_evidence["screenshots"].append(blob_url)
                    
                # Fetch logs for this execution
                log_blobs = self.container_client.list_blobs(name_starts_with=f"logs/{execution_id}")
                for blob in log_blobs:
                    blob_url = f"https://{self.blob_service_client.account_name}.blob.core.windows.net/{self.container_name}/{blob.name}"
                    execution_evidence["logs"].append(blob_url)
                    
                evidence_map[execution_id] = execution_evidence
                
        except Exception as e:
            logger.error(f"Failed to retrieve evidence URLs: {str(e)}")
        
        return evidence_map
    
    def cleanup_local_evidence(self, execution_ids: List[str], local_screenshots_dir: str = None):
        """
        Clean up local evidence files after uploading to Azure
        
        Args:
            execution_ids (List[str]): List of execution IDs
            local_screenshots_dir (str): Local screenshots directory
        """
        if not local_screenshots_dir:
            local_screenshots_dir = config.SCREENSHOTS_DIR
            
        if not os.path.exists(local_screenshots_dir):
            return
        
        try:
            # Remove screenshot files for completed executions
            for filename in os.listdir(local_screenshots_dir):
                for execution_id in execution_ids:
                    if execution_id in filename:
                        file_path = os.path.join(local_screenshots_dir, filename)
                        try:
                            os.remove(file_path)
                            logger.info(f"Cleaned up local file: {filename}")
                        except Exception as e:
                            logger.warning(f"Failed to remove {file_path}: {str(e)}")
                            
        except Exception as e:
            logger.error(f"Failed to cleanup local evidence: {str(e)}")
    
    def is_configured(self) -> bool:
        """Check if Azure Storage is properly configured"""
        return self.blob_service_client is not None and self.container_client is not None

# Fallback storage manager for local-only storage
class LocalStorageManager:
    """Local storage manager when Azure is not configured"""
    
    def __init__(self):
        self.storage_dir = "test_evidence"
        os.makedirs(self.storage_dir, exist_ok=True)
        os.makedirs(os.path.join(self.storage_dir, "screenshots"), exist_ok=True)
        os.makedirs(os.path.join(self.storage_dir, "reports"), exist_ok=True)
        os.makedirs(os.path.join(self.storage_dir, "logs"), exist_ok=True)
    
    def upload_screenshot(self, local_path: str, execution_id: str, state: str = "final") -> str:
        """Copy screenshot to local storage"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.basename(local_path)
        new_filename = f"{execution_id}_{state}_{timestamp}_{filename}"
        new_path = os.path.join(self.storage_dir, "screenshots", new_filename)
        
        try:
            import shutil
            shutil.copy2(local_path, new_path)
            return new_path
        except Exception as e:
            logger.error(f"Failed to copy screenshot: {str(e)}")
            return local_path
    
    def upload_test_report(self, html_content: str, report_id: str) -> str:
        """Save report to local storage"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{report_id}_{timestamp}.html"
        filepath = os.path.join(self.storage_dir, "reports", filename)
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(html_content)
            return filepath
        except Exception as e:
            logger.error(f"Failed to save report: {str(e)}")
            return None
    
    def upload_execution_evidence(self, execution) -> dict:
        """Upload all evidence from a test execution (local storage version)"""
        evidence_urls = {}
        
        # Copy screenshots to local storage
        for screenshot_path in execution.screenshots:
            if os.path.exists(screenshot_path):
                # Extract state from filename
                filename = os.path.basename(screenshot_path)
                state = "unknown"
                if "initial" in filename:
                    state = "initial"
                elif "final" in filename:
                    state = "final"
                elif "failure" in filename:
                    state = "failure"
                
                new_path = self.upload_screenshot(screenshot_path, execution.id, state)
                if new_path:
                    evidence_urls[screenshot_path] = new_path
        
        # Save logs as text file
        if execution.logs:
            try:
                log_content = "\n".join(execution.logs)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{execution.id}_{timestamp}.log"
                filepath = os.path.join(self.storage_dir, "logs", filename)
                
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(log_content)
                evidence_urls["logs"] = filepath
                
            except Exception as e:
                logger.error(f"Failed to save logs: {str(e)}")
        
        return evidence_urls
    
    def is_configured(self) -> bool:
        return True