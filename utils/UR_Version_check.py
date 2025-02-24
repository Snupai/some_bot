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
            chrome_options.add_argument('--headless=new')  # Using new headless mode
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-extensions')
            chrome_options.binary_location = '/snap/bin/chromium'
            chrome_options.add_argument('--remote-debugging-pipe')  # Use pipe instead of port
            chrome_options.add_argument('--single-process')  # Run in single process mode
            chrome_options.add_argument('--disable-features=site-per-process')  # Disable site isolation
            chrome_options.add_argument('--disable-setuid-sandbox')
            chrome_options.add_argument('--disable-dev-tools')
            chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])

            # Initialize ChromeDriver with the correct ChromeType and increased timeout
            service = Service(
                ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install(),
                service_args=['--verbose']
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
                cell_html = target_cell.get_attribute('innerHTML')
                self.logger.debug(f"Cell HTML: {cell_html}")
                
                version_links = []
                
                # Find all links in this cell
                links = target_cell.find_elements(By.TAG_NAME, "a")
                for link_element in links:
                    # Get the full HTML content of the link
                    link_html = link_element.get_attribute('innerHTML')
                    self.logger.debug(f"Link HTML: {link_html}")
                    
                    # Find all version numbers in the link HTML
                    versions = re.findall(self.version_pattern, link_html)
                    for version in versions:
                        if version.startswith(('5.', '6.', '7.', '8.', '9.')):
                            link = link_element.get_attribute("href")
                            if link:
                                version_links.append({"version": version, "link": link})
                                self.logger.debug(f"Added version from HTML: {version} with link: {link}")
                
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
