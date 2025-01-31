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
        self.url = "https://www.universal-robots.com/download/?filters[]=98763&filters[]=42714&filters[]=193962&query="
        self.version_file = Path("last_version.json")
        self.version_pattern = r"LATEST PolyScope Software Update - SW ([\d.]+) - e-Series and UR20/UR30"
        self.logger = logging.getLogger('bot.py')

    def load_last_version(self):
        if self.version_file.exists():
            with open(self.version_file, 'r') as f:
                data = json.load(f)
                return data.get('version')
        return None

    def save_version(self, version):
        data = {
            'version': version,
            'last_check': datetime.now().isoformat()
        }
        with open(self.version_file, 'w') as f:
            json.dump(data, f)

    async def check_version(self):
        try:
            # Set up Chrome options for headless operation
            chrome_options = Options()
            chrome_options.add_argument('--headless=new')  # Create a new browser context
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--remote-debugging-port=9222')
            chrome_options.add_argument('--disable-extensions')
            chrome_options.add_argument('--disable-setuid-sandbox')
            chrome_options.add_argument('--disable-software-rasterizer')
            chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])

            # Initialize ChromeDriver with the correct ChromeType for Chromium
            service = Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install())
            
            # Create a new Chrome driver instance
            driver = webdriver.Chrome(service=service, options=chrome_options)
            
            try:
                # Set window size explicitly
                driver.set_window_size(1920, 1080)
                
                driver.get(self.url)
                
                # Wait for and click the cookie consent button
                cookie_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.ID, "CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"))
                )
                cookie_button.click()
                
                # Wait for the button to be clickable and click it
                button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.CLASS_NAME, "button"))
                )
                button.click()
                
                # Give the page time to load (async-friendly)
                await asyncio.sleep(1)
                
                # Get the page content
                page_content = driver.page_source
                
                # Search for version in the content
                match = re.search(self.version_pattern, page_content)
                if match:
                    current_version = match.group(1)
                    last_version = self.load_last_version()
                    
                    if last_version != current_version:
                        self.logger.info(f"New version found: {current_version} (previous: {last_version})")
                        self.save_version(current_version)
                        return current_version
                    else:
                        self.logger.info(f"No new version. Current version: {current_version}")
                    return None
                else:
                    self.logger.info("Version pattern not found on the page")
                    return Exception("Version pattern not found on the page")
                
            finally:
                # Always clean up the driver
                driver.quit()
                
        except WebDriverException as e:
            self.logger.error(f"ChromeDriver error: {e}")
            return Exception(f"ChromeDriver error: {e}")
        except TimeoutException as e:
            self.logger.error(f"Timeout error: {e}")
            return Exception(f"Timeout error: {e}")
        except Exception as e:
            self.logger.error(f"Unexpected error: {e}")
            return Exception(f"Unexpected error: {e}")
