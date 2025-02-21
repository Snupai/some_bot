import re
import json
import logging
from datetime import datetime
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException, TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType
import asyncio

class URVersionChecker:
    def __init__(self):
        self.url = "https://www.universal-robots.com/articles/ur/documentation/legacy-download-center"
        self.version_file = Path("last_version.json")
        self.version_pattern = r"(\d+\.\d+\.\d+)"
        self.logger = logging.getLogger('bot.py')

    def load_last_version(self):
        if self.version_file.exists():
            with open(self.version_file, 'r') as f:
                data = json.load(f)
                return data.get('version')
        return None

    def save_version(self, version, link=None):
        data = {
            'version': version,
            'link': link,
            'last_check': datetime.now().isoformat()
        }
        with open(self.version_file, 'w') as f:
            json.dump(data, f)
            
    def get_latest_version(self, version_links):
        """Get the latest version from the list of version links"""
        if not version_links or isinstance(version_links, Exception):
            return None
            
        # Sort versions using semantic versioning
        sorted_versions = sorted(version_links, 
                               key=lambda x: [int(i) for i in x['version'].split('.')],
                               reverse=True)
        return sorted_versions[0] if sorted_versions else None

    async def check_version(self):
        try:
            # Set up Chrome options for headless operation
            chrome_options = Options()
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--remote-debugging-port=9222')  # Add specific debugging port
            chrome_options.add_argument('--disable-software-rasterizer')
            chrome_options.binary_location = '/snap/bin/chromium'
            chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
            chrome_options.add_experimental_option('w3c', True)  # Enable W3C mode

            # Initialize ChromeDriver with the correct ChromeType for Chromium
            service = Service(
                ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install()
            )
            
            # Create a new Chrome driver instance
            driver = webdriver.Chrome(
                service=service, 
                options=chrome_options
            )
            
            try:
                driver.set_page_load_timeout(30)
                driver.set_script_timeout(30)
                
                # Navigate to the URL
                driver.get(self.url)
                await asyncio.sleep(2)
                
                # Wait for and click the cookie consent button
                cookie_button = WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.ID, "CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"))
                )
                cookie_button.click()
                
                # Find the first table and navigate to the specific cell we want
                table = WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.TAG_NAME, "table"))
                )
                
                self.logger.debug("Found table, getting content...")
                
                # Get the second row in tbody
                tbody = table.find_element(By.TAG_NAME, "tbody")
                second_row = tbody.find_elements(By.TAG_NAME, "tr")[1]
                
                # Get the second cell (td) in that row
                target_cell = second_row.find_elements(By.TAG_NAME, "td")[1]
                #self.logger.debug(f"Target cell text: {target_cell.text}")
                
                version_links = []
                
                # Find all links in this cell
                links = target_cell.find_elements(By.TAG_NAME, "a")
                for link_element in links:
                    # Check both the link text and any spans inside it
                    link_text = link_element.text.strip()
                    #self.logger.debug(f"Found link text: {link_text}")
                    
                    # Try to find version number in link text
                    match = re.search(self.version_pattern, link_text)
                    if match:
                        version = match.group(1)
                        if version.startswith(('5.', '6.', '7.', '8.', '9.')):
                            link = link_element.get_attribute("href")
                            if link:
                                version_links.append({"version": version, "link": link})
                                #self.logger.debug(f"Added version: {version} with link: {link}")
                    
                    # Also check any spans inside this link
                    spans = link_element.find_elements(By.TAG_NAME, "span")
                    for span in spans:
                        span_text = span.text.strip()
                        #self.logger.debug(f"Found span text in link: {span_text}")
                        
                        match = re.search(self.version_pattern, span_text)
                        if match:
                            version = match.group(1)
                            if version.startswith(('5.', '6.', '7.', '8.', '9.')):
                                link = link_element.get_attribute("href")
                                if link:
                                    version_links.append({"version": version, "link": link})
                                    #self.logger.debug(f"Added version from span: {version} with link: {link}")
                
                # Remove duplicates while preserving order
                unique_versions = []
                seen = set()
                for item in version_links:
                    if item["version"] not in seen:
                        seen.add(item["version"])
                        unique_versions.append(item)
                
                #self.logger.debug(f"All found versions with links (after deduplication): {unique_versions}")
                
                if unique_versions:
                    self.logger.info(f"Found versions with links: {unique_versions}")
                    latest = self.get_latest_version(unique_versions)
                    last_version = self.load_last_version()
                    
                    if latest and (not last_version or latest['version'] != last_version):
                        self.logger.info(f"New version found: {latest['version']} (previous: {last_version})")
                        self.save_version(latest['version'], latest['link'])
                        return latest
                    else:
                        self.logger.info(f"No new version. Current version: {latest['version'] if latest else None}")
                        return None
                else:
                    self.logger.info("No valid versions found in e-Series column")
                    return Exception("No valid versions found in e-Series column")
                
            finally:
                driver.quit()
                
        except WebDriverException as e:
            self.logger.error(f"ChromeDriver error: {e}")
            return Exception(f"ChromeDriver error: {e}")
        except Exception as e:
            self.logger.error(f"Unexpected error: {e}")
            return Exception(f"Unexpected error: {e}")
