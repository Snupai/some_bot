import re
import time
import json
from datetime import datetime
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.core.os_manager import ChromeType
import logging
import asyncio

class URVersionChecker:
    def __init__(self):
        self.url = "https://www.universal-robots.com/download/?filters[]=98763&filters[]=42714&filters[]=193962&query="
        self.version_file = Path("last_version.json")
        self.version_pattern = r"LATEST PolyScope Software Update - SW ([\d.]+) - e-Series and UR20/UR30"
        # Setup Chrome options
        self.options = webdriver.ChromeOptions()
        self.options.add_argument('--headless=new')
        self.options.add_argument('--disable-gpu')
        self.options.add_argument('--no-sandbox')
        self.options.add_argument('--disable-dev-shm-usage')
        self.options.add_argument('--remote-debugging-port=9222')
        self.options.add_argument('--disable-extensions')
        self.options.add_argument('--disable-setuid-sandbox')
        self.options.add_argument('--disable-software-rasterizer')
        self.options.add_experimental_option('excludeSwitches', ['enable-logging'])
        
        # Try different possible Chrome binary locations
        chrome_paths = [
            '/usr/bin/google-chrome',
            '/usr/bin/google-chrome-stable',
            '/usr/bin/chromium-browser',
            '/usr/bin/chromium',
            'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
            'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
        ]
        for path in chrome_paths:
            if Path(path).exists():
                self.options.binary_location = path
                break
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
            # Initialize the driver with the correct ChromeType
            if '/usr/bin/chromium' in self.options.binary_location or 'chromium-browser' in self.options.binary_location:
                service = Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install())
            else:
                service = Service(ChromeDriverManager().install())
                
            driver = webdriver.Chrome(service=service, options=self.options)
            
            # Set window size explicitly
            driver.set_window_size(1920, 1080)

            driver.get(self.url)

            # Wait for and click the cookie consent button
            cookie_button = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.ID, "CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"))
            )
            cookie_button.click()

            # Wait for the button to be clickable and click it
            button = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CLASS_NAME, "button"))
            )
            button.click()

            # Wait for content to load
            await asyncio.sleep(1)  # Changed from time.sleep to asyncio.sleep
            
            # Get the page content
            page_content = driver.page_source
            
            # Close the browser
            driver.quit()

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

        except Exception as e:
            self.logger.error(f"Error checking version: {e}")
            return Exception("Error checking version")


if __name__ == "__main__":
    print("Dont run this file!")