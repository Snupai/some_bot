import re
import json
import logging
import time
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
import shutil
import os
import tempfile

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

    def parse_netscape_cookies(self, cookies_file='cookies.txt'):
        """
        Parse Netscape format cookies.txt file and return list of cookies in Selenium format.
        
        Netscape format: domain, flag, path, secure, expiration, name, value
        """
        cookies = []
        cookies_path = Path(cookies_file)
        
        if not cookies_path.exists():
            self.logger.warning(f"Cookies file {cookies_file} not found")
            return cookies
        
        try:
            with open(cookies_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    # Skip comments and empty lines
                    if not line or line.startswith('#'):
                        continue
                    
                    # Netscape format: domain, flag, path, secure, expiration, name, value
                    parts = line.split('\t')
                    if len(parts) >= 7:
                        domain = parts[0]
                        # flag = parts[1]  # TRUE if domain matches subdomains
                        path = parts[2]
                        secure = parts[3] == 'TRUE'
                        expiration = int(parts[4]) if parts[4] != '0' else None
                        name = parts[5]
                        value = parts[6]
                        
                        # Only include cookies for universal-robots.com domains
                        if 'universal-robots.com' in domain:
                            selenium_cookie = {
                                'name': name,
                                'value': value,
                                'domain': domain.lstrip('.') if domain.startswith('.') else domain,
                                'path': path,
                                'secure': secure,
                            }
                            # Only add expiry if it exists and is in the future
                            if expiration:
                                # Convert from milliseconds to seconds if needed
                                expiry_seconds = expiration // 1000 if expiration > 10000000000 else expiration
                                # Check if cookie hasn't expired
                                if expiry_seconds > time.time():
                                    selenium_cookie['expiry'] = expiry_seconds
                            
                            cookies.append(selenium_cookie)
            
            self.logger.info(f"Parsed {len(cookies)} cookies from {cookies_file} for universal-robots.com")
            return cookies
        except Exception as e:
            self.logger.error(f"Error parsing cookies file: {e}")
            return cookies

    def load_cookies_to_driver(self, driver, cookies_file='cookies.txt'):
        """Load cookies from cookies.txt file into Selenium driver"""
        cookies = self.parse_netscape_cookies(cookies_file)
        
        if not cookies:
            return False
        
        try:
            loaded_count = 0
            current_domain = driver.current_url.split('/')[2] if driver.current_url else 'www.universal-robots.com'
            
            for cookie in cookies:
                try:
                    domain = cookie.get('domain', '')
                    
                    # Remove leading dot for Selenium (it doesn't accept domain cookies with leading dot)
                    if domain.startswith('.'):
                        domain = domain[1:]
                    
                    cookie['domain'] = domain
                    
                    # Try to add the cookie - Selenium will reject it if domain doesn't match
                    driver.add_cookie(cookie)
                    loaded_count += 1
                    
                except Exception as e:
                    # Silently skip cookies that can't be added (wrong domain, expired, etc.)
                    self.logger.debug(f"Could not add cookie {cookie.get('name', 'unknown')}: {e}")
                    continue
            
            if loaded_count > 0:
                self.logger.info(f"Successfully loaded {loaded_count} cookies into driver")
            else:
                self.logger.warning("No cookies could be loaded (domain mismatch or expired)")
            
            return loaded_count > 0
        except Exception as e:
            self.logger.warning(f"Error loading cookies to driver: {e}")
            return False

    async def check_version(self, force=False):
        driver = None
        try:
            # Set up Chrome options for headless operation
            chrome_options = Options()
            chrome_options.add_argument('--headless=new')  # Using new headless mode
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-extensions')
            chrome_options.add_argument('--disable-setuid-sandbox')
            chrome_options.add_argument('--window-size=1920,1080')  # Set a decent window size
            
            # Try to find chromium in different locations
            chromium_candidates = [
                '/usr/bin/chromium',
                '/usr/bin/chromium-browser',
                '/usr/bin/google-chrome',
                '/usr/bin/google-chrome-stable',
                '/snap/bin/chromium'
            ]
            
            chrome_binary = None
            for candidate in chromium_candidates:
                if shutil.which(candidate) or os.path.exists(candidate):
                    chrome_binary = candidate
                    self.logger.info(f"Found Chrome/Chromium binary at: {chrome_binary}")
                    break
            
            if chrome_binary:
                chrome_options.binary_location = chrome_binary
            else:
                self.logger.warning("Could not find Chrome/Chromium binary in standard locations")
            
            # Create a temporary directory for Chrome data
            try:
                temp_dir = tempfile.mkdtemp(prefix="chrome-data-")
                self.logger.info(f"Created temporary Chrome data directory: {temp_dir}")
                chrome_options.add_argument(f"--user-data-dir={temp_dir}")
            except Exception as e:
                self.logger.warning(f"Failed to create temporary directory: {e}")
                # Fall back to a simple option without custom user data dir
                chrome_options.add_argument('--incognito')
            
            # Additional options to make it more stable
            chrome_options.add_argument('--disable-features=VizDisplayCompositor')
            chrome_options.add_argument('--disable-software-rasterizer')
            chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
            
            # Add a preference to disable images to make the page load faster
            prefs = {"profile.managed_default_content_settings.images": 2}
            chrome_options.add_experimental_option("prefs", prefs)

            # Initialize ChromeDriver with the correct ChromeType and increased timeout
            try:
                # First try system installed chromedriver
                driver_created = False
                chromedriver_path = shutil.which('chromedriver')
                
                if chromedriver_path:
                    try:
                        self.logger.info(f"Using system installed chromedriver: {chromedriver_path}")
                        service = Service(executable_path=chromedriver_path)
                        # Create a new Chrome driver instance
                        driver = webdriver.Chrome(service=service, options=chrome_options)
                        driver_created = True
                    except Exception as driver_err:
                        self.logger.warning(f"Failed to use system chromedriver: {driver_err}")
                        # Will fall back to webdriver_manager
                
                # If system driver failed or doesn't exist, use webdriver_manager
                if not driver_created:
                    try:
                        self.logger.info("Using webdriver_manager to install chromedriver")
                        service = Service(ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install())
                        # Create a new Chrome driver instance
                        driver = webdriver.Chrome(service=service, options=chrome_options)
                    except Exception as wdm_err:
                        self.logger.error(f"Failed to create driver with webdriver_manager: {wdm_err}")
                        # Last resort: try selenium's built-in webdriver management
                        self.logger.info("Using selenium built-in driver management as last resort")
                        driver = webdriver.Chrome(options=chrome_options)
                
                # Set page load timeout and script timeout
                driver.set_page_load_timeout(30)
                driver.set_script_timeout(30)
                
                # Navigate to the URL
                self.logger.info(f"Navigating to {self.url}")
                driver.get(self.url)
                await asyncio.sleep(2)
                
                # Load cookies from cookies.txt if available
                cookies_loaded = self.load_cookies_to_driver(driver, 'cookies.txt')
                if cookies_loaded:
                    driver.refresh()
                    await asyncio.sleep(2)
                    self.logger.info("Cookies loaded, refreshed page")
                
                # Try to find and click the cookie consent button
                try:
                    # Try multiple approaches to click the cookie button
                    # First, try to find the button
                    cookie_button = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.ID, "CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"))
                    )
                    
                    # Try approach 1: JavaScript click
                    try:
                        self.logger.info("Attempting to click cookie button with JavaScript")
                        driver.execute_script("arguments[0].click();", cookie_button)
                        self.logger.info("Successfully clicked cookie consent button with JavaScript")
                    except Exception as js_err:
                        self.logger.warning(f"JavaScript click failed: {js_err}")
                        
                        # Try approach 2: Try to scroll to the element first
                        try:
                            self.logger.info("Attempting to scroll to cookie button")
                            driver.execute_script("arguments[0].scrollIntoView(true);", cookie_button)
                            await asyncio.sleep(1)
                            cookie_button.click()
                            self.logger.info("Successfully clicked cookie consent button after scrolling")
                        except Exception as scroll_err:
                            self.logger.warning(f"Scroll and click failed: {scroll_err}")
                            
                            # Try approach 3: Try to handle overlaying iframe
                            try:
                                self.logger.info("Attempting to handle potential overlaying iframes")
                                # Find and hide any potential overlaying iframes
                                iframes = driver.find_elements(By.TAG_NAME, "iframe")
                                for iframe in iframes:
                                    driver.execute_script("arguments[0].style.visibility='hidden';", iframe)
                                
                                # Now try to click again
                                await asyncio.sleep(1)
                                cookie_button = WebDriverWait(driver, 5).until(
                                    EC.element_to_be_clickable((By.ID, "CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"))
                                )
                                cookie_button.click()
                                self.logger.info("Successfully clicked cookie consent button after handling iframes")
                            except Exception as iframe_err:
                                self.logger.warning(f"Failed to click after handling iframes: {iframe_err}")
                                # Continue anyway, as we might be able to access the content without accepting cookies
                
                except TimeoutException:
                    self.logger.warning("Cookie consent button not found or not clickable, continuing anyway")
                
                # Find the first table and navigate to the specific cell we want
                try:
                    # Wait for tables to be present
                    tables = WebDriverWait(driver, 30).until(
                        EC.presence_of_all_elements_located((By.TAG_NAME, "table"))
                    )
                    
                    self.logger.debug(f"Found {len(tables)} tables on the page")
                    
                    if not tables:
                        raise Exception("No tables found on the page")
                    
                    # Start with an empty list for version links
                    version_links = []
                    
                    # Try different approaches to find version information
                    # Approach 1: Try the original approach (second row, second cell of first table)
                    try:
                        table = tables[0]  # First table
                        self.logger.debug("Examining first table")
                        
                        # Get the second row in tbody if it exists
                        tbody = table.find_element(By.TAG_NAME, "tbody")
                        rows = tbody.find_elements(By.TAG_NAME, "tr")
                        
                        if len(rows) > 1:
                            second_row = rows[1]
                            cells = second_row.find_elements(By.TAG_NAME, "td")
                            
                            if len(cells) > 1:
                                target_cell = cells[1]  # Second cell
                                self.logger.debug("Found second cell in second row")
                                
                                # Find all links in this cell
                                links = target_cell.find_elements(By.TAG_NAME, "a")
                                
                                for link_element in links:
                                    link_html = link_element.get_attribute('innerHTML')
                                    versions = re.findall(self.version_pattern, link_html)
                                    for version in versions:
                                        if version.startswith(('5.', '6.', '7.', '8.', '9.')):
                                            link = link_element.get_attribute("href")
                                            if link:
                                                version_links.append({"version": version, "link": link})
                                                self.logger.debug(f"Added version from HTML: {version} with link: {link}")
                    except Exception as e:
                        self.logger.warning(f"Original table approach failed: {e}")
                    
                    # If the first approach didn't find any versions, try a more general approach
                    if not version_links:
                        self.logger.info("First approach failed, trying to scan all tables")
                        
                        # Approach 2: Scan all tables and their cells for version links
                        for table_idx, table in enumerate(tables):
                            try:
                                self.logger.debug(f"Scanning table {table_idx+1}")
                                
                                # Find all rows in this table
                                rows = table.find_elements(By.TAG_NAME, "tr")
                                
                                for row_idx, row in enumerate(rows):
                                    try:
                                        # Find all cells in this row
                                        cells = row.find_elements(By.TAG_NAME, "td")
                                        
                                        for cell_idx, cell in enumerate(cells):
                                            try:
                                                # Find all links in this cell
                                                links = cell.find_elements(By.TAG_NAME, "a")
                                                
                                                for link in links:
                                                    try:
                                                        link_html = link.get_attribute('innerHTML')
                                                        link_text = link.text
                                                        href = link.get_attribute("href")
                                                        
                                                        # Search for versions in both HTML and text
                                                        for content in [link_html, link_text]:
                                                            versions = re.findall(self.version_pattern, content)
                                                            for version in versions:
                                                                if version.startswith(('5.', '6.', '7.', '8.', '9.')):
                                                                    if href:
                                                                        version_links.append({"version": version, "link": href})
                                                                        self.logger.debug(f"Added version from table {table_idx+1}, row {row_idx+1}, cell {cell_idx+1}: {version} with link {href}")
                                                    except Exception as link_err:
                                                        self.logger.debug(f"Error processing link: {link_err}")
                                                        continue
                                                    
                                            except Exception as cell_err:
                                                self.logger.debug(f"Error processing cell: {cell_err}")
                                                continue
                                    
                                    except Exception as row_err:
                                        self.logger.debug(f"Error processing row: {row_err}")
                                        continue
                                        
                            except Exception as table_err:
                                self.logger.debug(f"Error processing table: {table_err}")
                                continue
                    
                    # If we still don't have any version links, try the most general approach
                    if not version_links:
                        self.logger.info("Table approaches failed, falling back to scanning all links")
                        version_links = self.fallback_version_find(driver)
                    
                except (TimeoutException, IndexError, Exception) as e:
                    error_msg = f"Error accessing table elements: {e}"
                    self.logger.error(error_msg)
                    
                    # Try fallback approach if the table structure didn't match
                    self.logger.info("Error in table detection, trying fallback approach")
                    version_links = self.fallback_version_find(driver)
                    
                    if not version_links:
                        # Try to save a screenshot for debugging
                        try:
                            screenshot_path = "error_screenshot.png"
                            driver.save_screenshot(screenshot_path)
                            self.logger.info(f"Saved error screenshot to {screenshot_path}")
                        except Exception as ss_err:
                            self.logger.warning(f"Could not save screenshot: {ss_err}")
                        
                        return Exception(error_msg)
                    
                # Remove duplicates while preserving order
                unique_versions = []
                seen = set()
                for item in version_links:
                    if item["version"] not in seen:
                        seen.add(item["version"])
                        unique_versions.append(item)
                
                # If we got here with no version links but no exception was raised,
                # the page structure might have changed
                if not unique_versions:
                    error_msg = "No version links found, page structure may have changed"
                    self.logger.error(error_msg)
                    return Exception(error_msg)
                
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
                        
                        # If force is True, return the current version even if it hasn't changed
                        if force and latest:
                            self.logger.info(f"Force parameter is True, returning current version: {latest['version']}")
                            return latest
                        
                        return None
                else:
                    self.logger.info("No valid versions found in e-Series column")
                    return Exception("No valid versions found in e-Series column")
                
            finally:
                # Safely quit the driver if it was initialized
                if driver:
                    try:
                        driver.quit()
                    except Exception as e:
                        self.logger.warning(f"Error closing webdriver: {e}")
                
                # Clean up the temporary directory if it exists
                try:
                    if 'temp_dir' in locals() and os.path.exists(temp_dir):
                        shutil.rmtree(temp_dir, ignore_errors=True)
                        self.logger.info(f"Cleaned up temporary directory: {temp_dir}")
                except Exception as e:
                    self.logger.warning(f"Error cleaning up temporary directory: {e}")
                
        except WebDriverException as e:
            self.logger.error(f"ChromeDriver error: {e}")
            return Exception(f"ChromeDriver error: {e}")
        except Exception as e:
            self.logger.error(f"Unexpected error: {e}")
            return Exception(f"Unexpected error: {e}")

    def fallback_version_find(self, driver):
        """Fallback method to find version links anywhere on the page"""
        version_links = []
        try:
            self.logger.info("Attempting to find versions with fallback method")
            
            # Look for links anywhere on the page
            links = driver.find_elements(By.TAG_NAME, "a")
            self.logger.info(f"Found {len(links)} links to search through")
            
            # Get the page source for debugging if no links are found
            if not links:
                page_source = driver.page_source
                with open("page_source.html", "w", encoding="utf-8") as f:
                    f.write(page_source)
                self.logger.info("Saved page source to page_source.html for debugging")
            
            # Find links that contain version numbers
            for link in links:
                try:
                    href = link.get_attribute("href")
                    text = link.text
                    
                    # Skip links without href or text
                    if not href or not text:
                        continue
                    
                    # Extract version from link text
                    versions = re.findall(self.version_pattern, text)
                    for version in versions:
                        if version.startswith(('5.', '6.', '7.', '8.', '9.')):
                            version_links.append({"version": version, "link": href})
                            self.logger.info(f"Fallback: Found version {version} with link {href}")
                            
                except Exception as link_err:
                    # Just log and continue if one link causes an error
                    self.logger.warning(f"Error processing link in fallback method: {link_err}")
                    continue
            
            self.logger.info(f"Fallback method found {len(version_links)} version links")
            return version_links
            
        except Exception as e:
            self.logger.error(f"Error in fallback version finder: {e}")
            return []
