import time
import re
import os
import json
import argparse
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException, StaleElementReferenceException
import undetected_chromedriver as uc
from selenium.webdriver.chrome.options import Options
import logging
from urllib.parse import urlparse
import requests
from openai import OpenAI

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
client = OpenAI(
    base_url="https://api.deepseek.com/v1",
    api_key= os.getenv("DEEPSEEK_API_KEY")
)
# Create results directory
os.makedirs("shopping_output", exist_ok=True)

def extract_with_deepseek(html_content, target_field):
    """
    Use DeepSeek API to extract missing product information from HTML content
    
    Args:
        html_content: HTML content of the page
        target_field: Field to extract ('price' or 'title')
        
    Returns:
        str: Extracted value or None if extraction failed
    """
        
    print(f"Calling DeepSeek API to extract missing {target_field}...")
    
    # Truncate HTML content if too large (most APIs have request size limits)
    if len(html_content) > 100000:
        html_content = html_content[:100000] + "..."
    
    # Prepare prompt based on target field
    if target_field == 'price':
        prompt = """Extract the product price from this HTML content. 
        Return only the price as a string with a $ symbol (e.g. $99.99).
        If you cannot find the price, return None."""
    elif target_field == 'title':
        prompt = """Extract the product title from this HTML content.
        Return only the title as a string. Do not include any other text.
        If you cannot find the title, return None."""
    else:
        return None
    
    try:

        # Call the API using the client
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "You are a precise HTML content extractor. Extract exactly what is asked for from the HTML."},
                {"role": "user", "content": f"{prompt}\n\nHTML Content: {html_content}"}
            ],
            temperature=0.1,  # Low temperature for more deterministic responses
            max_tokens=50     # We only need a short response
        )
        
        # Extract the response content
        if response and response.choices:
            extracted_text = response.choices[0].message.content.strip()
            
            # Process the response
            if extracted_text.lower() == "none" or not extracted_text:
                return None
                
            print(f"DeepSeek API extracted {target_field}: {extracted_text}")
            return extracted_text
        else:
            print(f"DeepSeek API returned empty response")
            return None
            
    except Exception as e:
        print(f"Error calling DeepSeek API: {e}")
        return None

class ProductScraper:
    def __init__(self, headless=False):
        """Initialize scraper"""
        options = Options()
        if headless:
            options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--start-maximized')
        
        # Initialize browser
        self.driver = uc.Chrome(version_main=135, options=options)
        self.driver.implicitly_wait(10)
        self.results = []
        
    def __del__(self):
        """Close browser"""
        if hasattr(self, 'driver'):
            self.driver.quit()
    
    def find_search_box(self):
        """Try multiple methods to find the search box"""
        # Common search box attribute list
        search_selectors = [
            # Common name attributes
            (By.NAME, "q"), (By.NAME, "query"), (By.NAME, "search"), (By.NAME, "searchTerm"), (By.NAME, "keyword"),
            # Common placeholder attributes
            (By.XPATH, "//input[contains(@placeholder, 'search')]"),
            # Leave out the Chinese placeholder search
            # Common IDs and classes
            (By.ID, "search"), (By.ID, "searchbox"), (By.ID, "search-input"),
            (By.CSS_SELECTOR, ".search-box"), (By.CSS_SELECTOR, ".search-input"),
            # Common role attributes
            (By.CSS_SELECTOR, "[role='search'] input"),
            # Data attributes
            (By.CSS_SELECTOR, "[data-test*='search'] input"),
            (By.CSS_SELECTOR, "[data-testid*='search'] input"),
            # General input fields
            (By.TAG_NAME, "input")
        ]
        
        # Try to find the search box
        for selector_type, selector in search_selectors:
            try:
                elements = self.driver.find_elements(selector_type, selector)
                for element in elements:
                    if element.is_displayed() and element.get_attribute("type") != "hidden":
                        # Check if this is a search box
                        if self._is_likely_search_box(element):
                            return element
            except:
                continue
        
        return None
    
    def _is_likely_search_box(self, element):
        """Determine if an element is likely a search box"""
        # Check element attributes
        element_type = element.get_attribute("type")
        if element_type in ["search", "text"]:
            return True
            
        # Check element ID or class name
        element_id = element.get_attribute("id") or ""
        element_class = element.get_attribute("class") or ""
        search_terms = ["search", "query", "keyword", "find"]
        
        for term in search_terms:
            if term in element_id.lower() or term in element_class.lower():
                return True
                
        # Check placeholder text
        placeholder = element.get_attribute("placeholder") or ""
        if any(term in placeholder.lower() for term in search_terms):
            return True
            
        return False
    
    def _get_main_link_from_container(self, container_element):
        logger.info(f"  Enter _get_main_link_from_container for element: {container_element.tag_name} class='{container_element.get_attribute('class')}'")
        try:
            if container_element.tag_name == 'a' and container_element.get_attribute('href'):
                logger.info("    Priority 1: Container is itself a link.")
                if container_element.is_displayed():
                    logger.info(f"      Container is itself a link and visible: {container_element.get_attribute('href')}")
                    return container_element
            
            
            logger.info("    Priority 3: Looking for other prominent links...")
            all_links_in_container = container_element.find_elements(By.TAG_NAME, 'a')
            logger.info(f"    Priority 3: Found {len(all_links_in_container)} 'a' tags in container.")
            potential_links = []
            for link_idx, link in enumerate(all_links_in_container):
                logger.info(f"      P3 Link {link_idx+1}: Checking text and size...")
                href = link.get_attribute('href')
                if link.is_displayed() and href and "javascript:void(0)" not in href:
                    text_content = link.text.strip()
                    title_attr = link.get_attribute('title') or ""
                    if len(text_content) > 5 or len(title_attr) > 5:
                        logger.info(f"        P3 Link {link_idx+1} has significant text/title.")
                        potential_links.append(link)
                    elif self._is_reasonable_size(link, check_parent=True, parent_container=container_element):
                        logger.info(f"        P3 Link {link_idx+1} has reasonable size.")
                        potential_links.append(link)
                    else:
                        logger.info(f"        P3 Link {link_idx+1} no significant text/title and not reasonable size.")
            
            if potential_links:
                potential_links.sort(key=lambda l: (len(l.text.strip()), l.size['width'] * l.size['height'] if l.size else 0), reverse=True)
                logger.info(f"    Priority 3: Sorted {len(potential_links)} potential links. Top one: {potential_links[0].get_attribute('href')}")
                return potential_links[0]
            else:
                logger.info("    Priority 3: No potential links found after filtering.")

        except NoSuchElementException:
            logger.info("  _get_main_link_from_container: NoSuchElementException")
        except Exception as e:
            logger.error(f"  _get_main_link_from_container error: {e}", exc_info=True)
        logger.info(f"  Exit _get_main_link_from_container, returning None")
        return None

    def _is_reasonable_size(self, element, check_parent=False, parent_container=None):
        try:
            # First check if element is visible
            if not element.is_displayed():
                return False
                
            # Get element size
            size = element.size
            
            # Check element's own size
            if size['width'] < 40 or size['height'] < 40:  # Minimum reasonable size threshold
                # If element is small but it's a link and parent container size is reasonable, could still be valid
                if check_parent and parent_container and element.tag_name == 'a':
                    try:
                        parent_size = parent_container.size
                        if parent_size['width'] > 50 and parent_size['height'] > 50:
                            return True  # Parent container size is reasonable, consider it a valid element
                    except:
                        pass
                return False  # Element size too small
                
            return True
            
        except Exception as e:
            logger.info(f"Error checking element size: {e}")
            return False  # Default to False on error

    def find_products(self):
        min_products_for_list = 10  
        # candidate_product_links = [] # Not needed here as we return on first success

        # 1. Count CSS class frequency
        class_counts = {}
        elements_to_analyze = self.driver.find_elements(By.XPATH, "//li | //div | //article | //section")
        logger.info(f"Analyzing {len(elements_to_analyze)} major elements on the page to find repeated classes...")

        for element in elements_to_analyze:
            try:
                if not element.is_displayed():
                    continue
                classes_str = element.get_attribute("class")
                if classes_str:
                    sorted_classes = tuple(sorted(filter(None, classes_str.split())))
                    if not sorted_classes:
                        continue
                    class_counts[sorted_classes] = class_counts.get(sorted_classes, 0) + 1
            except Exception as e:
                logger.info(f"Error getting element class: {e}")
                continue
        
        sorted_frequent_classes = sorted([
            (k,v) for k,v in class_counts.items() if v >= min_products_for_list
        ], key=lambda item: item[1], reverse=True)
        
        logger.info(f"Found {len(sorted_frequent_classes)} class combinations with frequency >= {min_products_for_list}")
        print(sorted_frequent_classes)
        if not sorted_frequent_classes:
            logger.warning("Could not find CSS class combinations with sufficient frequency.")
        
        for class_tuple, count in sorted_frequent_classes:
            if not class_tuple: continue
            class_selector_value = "." + ".".join(class_tuple)
            class_selector_type = By.CSS_SELECTOR
            logger.info(f"Trying high frequency class combination: '{class_selector_value}' (occurs {count} times)")
            try:
                potential_containers = self.driver.find_elements(class_selector_type, class_selector_value)
                visible_containers = potential_containers
                logger.info(f"For class '{class_selector_value}', found {len(visible_containers)} visible containers with reasonable size")

                if len(visible_containers) >= min_products_for_list:
                    links_from_this_class_group = []
                    processed_hrefs_for_group = set()
                    logger.info(f"Starting to process {len(visible_containers)} visible containers (class: '{class_selector_value}')...")
                    consecutive_none_returns = 0 # Initialize consecutive None counter
                    scroll_step = self.driver.execute_script("return window.innerHeight") 
                    last_main_link = None
                    for idx, container in enumerate(visible_containers):
                        logger.info(f"  Container {idx+1}/{len(visible_containers)}: Starting to call _get_main_link_from_container")
                        main_link = self._get_main_link_from_container(container)
                        logger.info(f"  Container {idx+1}/{len(visible_containers)}: _get_main_link_from_container returned {'link' if main_link else 'None'}")
                        
                        if main_link:
                            consecutive_none_returns = 0 # Reset counter
                            href = main_link.get_attribute('href')
                            if href and href not in processed_hrefs_for_group:
                                links_from_this_class_group.append(main_link)
                                processed_hrefs_for_group.add(href)
                            last_main_link = main_link
                        else:
                            # Only try scrolling if previous was successful but current failed
                            if last_main_link is not None:
                                # Continue scrolling until link is found or bottom is confirmed
                                max_scroll_attempts = 5  # Maximum scroll attempts
                                scroll_attempts = 0
                                initial_height = self.driver.execute_script("return document.body.scrollHeight")
                                
                                while scroll_attempts < max_scroll_attempts:
                                    # Scroll a bit and wait
                                    self.driver.execute_script(f"window.scrollBy(0, {scroll_step});")
                                    logger.info(f"Previous container was successful but container {idx+1} failed, scroll attempt {scroll_attempts+1}/{max_scroll_attempts}")
                                    time.sleep(0.7)  # Give page time to load
                                    
                                    # Check if we've reached bottom of page
                                    new_height = self.driver.execute_script("return document.body.scrollHeight")

                                    
                                    # Try to get link again
                                    main_link = self._get_main_link_from_container(container)
                                    logger.info(f"  Container {idx+1} after scroll retry #{scroll_attempts+1}: {'successful' if main_link else 'failed'}")
                                    
                                    if main_link:  # Successfully got link, exit loop
                                        consecutive_none_returns = 0
                                        href = main_link.get_attribute('href')
                                        if href and href not in processed_hrefs_for_group:
                                            links_from_this_class_group.append(main_link)
                                            processed_hrefs_for_group.add(href)
                                        last_main_link = main_link
                                        break
                                    
                                    initial_height = new_height
                                    scroll_attempts += 1
                                
                                # Only count as None if all scroll attempts failed
                                if not main_link:
                                    consecutive_none_returns += 1
                                    logger.info(f"    After {scroll_attempts} scroll attempts still no link, consecutive None returns: {consecutive_none_returns}")
                                    last_main_link = None
                            else:
                                # Previous was None, this is None, count directly
                                consecutive_none_returns += 1
                                logger.info(f"    Previous container failed too, consecutive None returns: {consecutive_none_returns}")
                                last_main_link = None

                        if consecutive_none_returns >= 2:
                            logger.warning(f"    Failed to extract main link from containers 3 consecutive times. Skipping remaining containers for class combination '{class_selector_value}'.")
                            break # Break out of container processing for this frequent class
                        
                    
                    # After inner loop ends (whether normal end or break), check if enough links collected
                    if len(links_from_this_class_group) >= min_products_for_list:
                        logger.info(f"Success! Found {len(links_from_this_class_group)} product links through high frequency class '{class_selector_value}'.")
                        return links_from_this_class_group, class_selector_type, class_selector_value
                else:
                    logger.info(f"Class '{class_selector_value}' has insufficient visible containers ({len(visible_containers)}/{min_products_for_list})")        
            except Exception as e:
                logger.warning(f"Error processing class '{class_selector_value}': {e}")
                continue
        
        logger.warning("All major product list finding strategies failed. Returning None")
        return None, None, None

    def find_next_page_button(self, exclude_buttons=None, return_all=False):
        """Find next page button, supporting exclusion of already tried buttons
        
        Args:
            exclude_buttons: Set of buttons to exclude, like already tried ones
            return_all: If True, return all matching buttons list; otherwise return first matching button
            
        Returns:
            If return_all=False, returns the first button found or None
            If return_all=True, returns list of all buttons found
        """
        if exclude_buttons is None:
            exclude_buttons = set()
            
        # Common next page button selectors
        next_button_selectors = [
            # Find by data-testid or data-test attributes
            (By.CSS_SELECTOR, "[data-testid='NextPage'], [data-test='next'], [data-testid*='next']"),
            # Find by aria-label attribute (case insensitive)
            (By.CSS_SELECTOR, "[aria-label*='next' i], [aria-label*='Next' i]"),
            # Next page related class names
            (By.CSS_SELECTOR, "[class*='next'], .styles_next"),
            (By.XPATH, "//a[.//i[contains(@class, 'ChevronRight')]] | //button[.//svg]"),
            (By.XPATH, "//*[contains(translate(text(), 'NEXT', 'next'), 'next')]"),
            # Generic pagination buttons
            (By.CSS_SELECTOR, ".pagination .next, .pagination-next"),
        ]
        
        if return_all:
            all_buttons = []
        
        # Try to find next page button
        for selector_type, selector in next_button_selectors:
            try:
                elements = self.driver.find_elements(selector_type, selector)
                for element in elements:
                    # Skip already excluded buttons
                    if element in exclude_buttons:
                        continue
                        
                    if element.is_displayed() and element.is_enabled():
                        if return_all:
                            all_buttons.append(element)
                        else:
                            logger.info(f"Found next page button: {element.tag_name}")
                            return element
            except Exception as e:
                continue
                
        if return_all:
            return all_buttons
        return None
        
    def find_alternative_next_buttons(self, exclude_buttons=None):
        return self.find_next_page_button(exclude_buttons=exclude_buttons, return_all=True)
    
    def extract_product_info(self):
        product_info = {}
        
        title_selectors = [
            (By.CSS_SELECTOR, "h1"), 
            (By.CSS_SELECTOR, ".product-title, .product-name")
        ]
        
        # Try to get price
        price_selectors = [
            # Find by itemprop="price" attribute
            (By.CSS_SELECTOR, "[itemprop='price']"),
            (By.XPATH, "//*[contains(text(), '$')]"),
            # Find by data-testid="price-wrap"
            (By.CSS_SELECTOR, "[data-testid='price-wrap']"),
            # Original selectors
            (By.CSS_SELECTOR, ".price, .product-price"),
            (By.CSS_SELECTOR, "[data-test*='price'], [data-testid*='price']"),
            (By.XPATH, "//span[contains(@class, 'price')]")
        ]
        
        # Get title
        for selector_type, selector in title_selectors:
           
            try:
                title_element = self.driver.find_element(selector_type, selector)
                if title_element.is_displayed():
                    product_info['title'] = title_element.text.strip()
                    break
                else:
                    print("Title element found but not displayed")
            except Exception as e:
                print(f"Error finding title with selector '{selector}': {e}")
                continue
                
        # Get price
        for selector_type, selector in price_selectors:
            try:
                print(f"Trying price selector: {selector_type}, '{selector}'")
                price_element = self.driver.find_element(selector_type, selector)
                if price_element.is_displayed():
                    price_text = price_element.text.strip()
                    print(f"Found price element with text: '{price_text}'")
                    # If price text not directly available (might be wrapper element), try to find element with $ inside
                    if not price_text or '$' not in price_text:
                        print("Price text empty or doesn't contain '$', searching in nested elements...")
                        try:
                            # Find elements containing $ inside current element
                            nested_price_elements = price_element.find_elements(By.XPATH, ".//*[contains(text(), '$')]")
                            print(f"Found {len(nested_price_elements)} nested elements with '$' symbol")
                            for i, nested_element in enumerate(nested_price_elements):
                                if nested_element.is_displayed():
                                    price_text = nested_element.text.strip()
                                    print(f"Nested element #{i+1} text: '{price_text}'")
                                    if '$' in price_text:
                                        print(f"Using price from nested element #{i+1}")
                                        break
                        except Exception as e:
                            print(f"Error searching nested elements: {e}")
                    
                    # Try to extract price
                    print(f"Attempting to extract price pattern from: '{price_text}'")
                    price_match = re.search(r'(\$\d+(\.\d+)?)', price_text)
                    if price_match:
                        product_info['price'] = price_match.group(1)
                        print(f"Successfully extracted price: {product_info['price']}")
                    else:
                        product_info['price'] = price_text
                        print(f"No price pattern found, using full text: {product_info['price']}")
                    break
            except Exception as e:
                print(f"Error with price selector '{selector}': {e}")
                continue
                
        # Get URL
        product_info['url'] = self.driver.current_url
        
        # Use DeepSeek API for missing information if URL is available
        if product_info.get('url'):
            # If title is missing, try to extract it with DeepSeek
            if not product_info.get('title'):
                print("Title not found using selectors, trying DeepSeek API...")
                try:
                    # Get page HTML
                    page_html = self.driver.page_source
                    extracted_title = extract_with_deepseek(page_html, 'title')
                    if extracted_title:
                        product_info['title'] = extracted_title
                        print(f"Successfully extracted title with DeepSeek API: {extracted_title}")
                except Exception as e:
                    print(f"Error using DeepSeek API for title extraction: {e}")
            
            # If price is missing, invalid, or $0.00, try to extract it with DeepSeek
            has_valid_price = False
            if product_info.get('price'):
                # Check if price has $ and digits
                price_str = product_info.get('price', '')
                if re.search(r'\$\d+', price_str):
                    # If price is $0.00, still consider it invalid
                    if price_str == '$0.00' :
                        print("Price is $0.00, considering as invalid price")
                        has_valid_price = False
                    else:
                        has_valid_price = True
                    
            if not has_valid_price:
                print("Valid price not found or price is $0.00, trying DeepSeek API...")
                try:
                    # Get page HTML
                    page_html = self.driver.page_source
                    extracted_price = extract_with_deepseek(page_html, 'price')
                    if extracted_price:
                        product_info['price'] = extracted_price
                        print(f"Successfully extracted price with DeepSeek API: {extracted_price}")
                except Exception as e:
                    print(f"Error using DeepSeek API for price extraction: {e}")
        
        return product_info
        
    def search_and_scrape(self, starting_website, search_term="PlayStation", max_pages=5, max_products_per_page=100, scroll_speed="slow"):
        """Search for products and scrape details. Results will be saved to a separate JSON file for that website.
        
        Args:
            starting_website: Starting website URL
            search_term: Search keyword
            max_pages: Maximum pages to scrape
            max_products_per_page: Maximum products to scrape per page
            scroll_speed: Scroll speed, "slow" for slow scrolling, "fast" for fast scrolling
        """
        logger.info(f"Starting to scrape product information from {starting_website}")
        domain = urlparse(starting_website).netloc
        # Track successfully scraped products count
        products_count = 0
        
        try:
            self.driver.get(starting_website)
            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            
            logger.info("Looking for search box...")
            search_box = self.find_search_box()
            if not search_box:
                logger.error("Cannot find search box")
                return 0  # Return scrape count as 0
                
            logger.info(f"Searching for {search_term}...")
            search_box.clear()
            search_box.send_keys(search_term)
            search_box.send_keys(Keys.RETURN)
            time.sleep(8) # Wait after search
            
            for page_num in range(max_pages):
                logger.info(f"Processing page {page_num + 1} results")
                
                logger.info("Page scrolling complete.")
                
                product_elements, successful_selector_type, successful_selector_value = self.find_products()
                
                if not product_elements:
                    logger.warning(f"No product list found on page {page_num + 1} or major strategy failed")
                    break 
                    
                logger.info(f"Found {len(product_elements)} products (using selector: {successful_selector_type}, '{successful_selector_value}')")
                products_to_process_on_this_page = min(max_products_per_page, len(product_elements))
                logger.info(f"Will process {products_to_process_on_this_page} products on this page")
                processed_product_hrefs_on_page = set()

                for i in range(products_to_process_on_this_page):
                    current_product_element_to_click = None
                    product_description_for_log = f"Product #{i+1} (Total #{len(processed_product_hrefs_on_page) + 1})"
                    try:
                        if i > 0 and successful_selector_type and successful_selector_value:
                            logger.info(f"Re-finding product containers to get product #{i+1} (using selector: {successful_selector_type}, '{successful_selector_value}')")
                            refreshed_container_elements = self.driver.find_elements(successful_selector_type, successful_selector_value)
                            if i < len(refreshed_container_elements):
                                specific_container = refreshed_container_elements[i]
                                logger.info(f"Got product #{i+1} refreshed container, trying to extract main link...")
                                try:
                                    main_link = self._get_main_link_from_container(specific_container)
                                    if main_link:
                                        current_product_element_to_click = main_link
                                        product_description_for_log += " [from refreshed list-single extraction]"
                                    else:
                                        logger.warning(f"Failed to extract main link from refreshed container #{i+1}.")
                                except StaleElementReferenceException:
                                    logger.warning(f"Stale element when trying to operate on refreshed container #{i+1} to extract link.")
                                except Exception as e_extract:
                                    logger.error(f"Unknown error extracting link from refreshed container #{i+1}: {e_extract}")
                            else: 
                                logger.warning(f"After refreshing containers, index {i} out of range (found {len(refreshed_container_elements)} containers)")
                        else:
                            if i < len(product_elements):
                                current_product_element_to_click = product_elements[i]
                                product_description_for_log += " [from initial list]"
                            else:
                                logger.warning(f"Initial product list index {i} out of range (list length {len(product_elements)}) ")
                                current_product_element_to_click = None

                        if not current_product_element_to_click:
                            logger.warning(f"Could not locate clickable element for {product_description_for_log}, skipping this product")
                            continue 

                        target_href = current_product_element_to_click.get_attribute('href')
                        if target_href in processed_product_hrefs_on_page:
                            logger.info(f"Product {product_description_for_log} (URL: {target_href}) already processed on this page, skipping")
                            continue

                        logger.info(f"Clicking product {product_description_for_log} (URL: {target_href})")
                        main_window = self.driver.current_window_handle
                        try:
                            current_product_element_to_click.click()
                        except ElementClickInterceptedException:
                            logger.warning("Regular click intercepted, trying JS click")
                            self.driver.execute_script("arguments[0].click();", current_product_element_to_click)
                            
                        new_window = None
                        if len(self.driver.window_handles) > 1:
                            for handle in self.driver.window_handles:
                                if handle != main_window:
                                    new_window = handle
                                    self.driver.switch_to.window(new_window)
                                    logger.info("Switched to new window")
                                    break
                        # Scroll page before extracting product info to ensure content is fully loaded
                        time.sleep(1)
                        try:
                            # Scroll to middle of page
                            self.driver.execute_script("window.scrollBy(0, 300);")
                            logger.info("Small scroll on product detail page to trigger lazy-loaded content")
                            time.sleep(1)
                        except Exception as e:
                            logger.warning(f"Product page scrolling failed: {e}")
                        
                        logger.info("Extracting product information")
                        product_info = self.extract_product_info()
                        
                        if product_info.get('url'): 
                           processed_product_hrefs_on_page.add(target_href) 
                           
                           # Use jsonlines format, append new product directly
                           json_file_path = f"shopping_output/{domain}_products.jsonl"
                           with open(json_file_path, "a", encoding="utf-8") as f:
                               # If file not empty, add newline first
                               if os.path.exists(json_file_path) and os.path.getsize(json_file_path) > 0:
                                   f.write("\n")
                               # Write single JSON object (one line)
                               json.dump(product_info, f, ensure_ascii=False)
                           
                           products_count += 1  # Increment successful product scrape counter
                           print(f"Appended new product to {json_file_path}: {product_info}")
                        else:
                            logger.warning("Extracted product info incomplete (missing URL), not saved")

                        logger.info("About to handle window operations or navigate back...")
                        if new_window:
                            logger.info("Detected new window, preparing to close and switch...")
                            self.driver.close()
                            logger.info("New window closed, preparing to switch to main window...")
                            self.driver.switch_to.window(main_window)
                            logger.info("Switched to main window.")
                        else:
                            logger.info("No new window, executing driver.back()...")
                            self.driver.back()
                            logger.info("driver.back() executed.")
                        
                        logger.info("Preparing to execute time.sleep(3)...")
                        time.sleep(3) 
                        logger.info("time.sleep(3) ended. Loop about to enter next iteration or end.")
                        
                    except StaleElementReferenceException as e_stale:
                        logger.error(f"StaleElementReferenceException when processing product {product_description_for_log}: {e_stale} - trying to move to next page")
                        break 
                    except Exception as e:
                        logger.error(f"Error processing product {product_description_for_log}: {e}", exc_info=True)
                        try:
                            if new_window and self.driver.current_window_handle == new_window: 
                                self.driver.close()
                                self.driver.switch_to.window(main_window)
                            elif self.driver.current_url != starting_website and "search" not in self.driver.current_url.lower():
                                self.driver.back()
                            time.sleep(2)
                        except Exception as e_nav:
                            logger.error(f"Failed to navigate back to listing page during error handling: {e_nav}")
                            break 
                
                if page_num < max_pages - 1:
                    logger.info("Looking for next page button...")
                    next_button = self.find_next_page_button()
                    if next_button:
                        logger.info("Starting to try next page button")
                        current_url = self.driver.current_url
                        tried_buttons = set()
                        
                        # Try to click the found button
                        success, new_url = self.try_next_page_button(next_button, current_url, tried_buttons)
                        
                        # If click fails, try other possible buttons
                        if not success:
                            logger.info("Main button click failed, looking for alternative buttons...")
                            alternative_buttons = self.find_alternative_next_buttons(tried_buttons)
                            
                            if alternative_buttons:
                                logger.info(f"Found {len(alternative_buttons)} alternative buttons")
                                for alt_button in alternative_buttons:
                                    success, new_url = self.try_next_page_button(alt_button, current_url, tried_buttons)
                                    if success:
                                        logger.info("Found valid alternative next page button")
                                        break
                                
                                if not success:
                                    logger.warning(f"Tried {len(tried_buttons)} buttons, all failed to navigate to next page")
                                    break  # End page loop
                            else:
                                logger.warning("No other possible next page buttons found")
                                break  # End page loop
                    else:
                        logger.info("No next page button found or already at last page")
                        break 
                else:
                    logger.info("Reached maximum scrape page count")
                    break

        except Exception as e:
            logger.error(f"Top-level error during search and scrape ({starting_website}): {e}", exc_info=True)
            
        logger.info(f"Scraping on {starting_website} complete, scraped {products_count} valid products")
        return products_count  # Return scrape count

    def try_next_page_button(self, button, current_url=None, tried_buttons=None):
        """Try to click next page button and verify URL change
        
        Args:
            button: Button element to click
            current_url: Current URL, if None will be auto-retrieved
            tried_buttons: Set of already tried buttons, to avoid duplicate clicks
            
        Returns:
            (success flag, new URL) tuple, success flag True indicates URL has changed
        """
        if current_url is None:
            current_url = self.driver.current_url
            
        if tried_buttons is None:
            tried_buttons = set()
            
        # Skip already tried buttons
        if button in tried_buttons:
            logger.info("Skipping already tried button")
            return False, current_url
            
        tried_buttons.add(button)
        
        # Try to click button
        try:
            logger.info(f"Trying to click {button.tag_name} button")
            button.click()
            time.sleep(3)  # Wait for page to load
            
            # Check if URL has changed
            new_url = self.driver.current_url
            if new_url != current_url:
                logger.info(f"Success! URL has changed: {new_url}")
                return True, new_url
            else:
                logger.warning("URL didn't change after button click, this may not be a true pagination button")
                return False, current_url
                
        except ElementClickInterceptedException:
            logger.warning("Button click intercepted, trying JS click")
            try:
                self.driver.execute_script("arguments[0].click();", button)
                time.sleep(3)
                
                # Check if URL changed after JS click
                new_url = self.driver.current_url
                if new_url != current_url:
                    logger.info(f"JS click success! URL has changed: {new_url}")
                    return True, new_url
                else:
                    logger.warning("URL didn't change after JS button click")
                    return False, current_url
            except Exception as e:
                logger.error(f"JS button click failed: {e}")
                return False, current_url
        except Exception as e:
            logger.warning(f"Error clicking button: {e}")
            return False, current_url

# Execute script
if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Web scraper for e-commerce websites')
    parser.add_argument('--urls', nargs='+', required=True, help='List of website URLs to scrape')
    parser.add_argument('--search', default="PlayStation", help='Search term (default: PlayStation)')
    parser.add_argument('--headless', action='store_true', help='Run in headless mode')
    parser.add_argument('--max-pages', type=int, default=5, help='Maximum number of pages to scrape per website (default: 5)')
    parser.add_argument('--max-products', type=int, default=100, help='Maximum number of products to scrape per page (default: 100)')
    parser.add_argument('--scroll-speed', choices=['slow', 'fast'], default='slow', help='Page scrolling speed (default: slow)')
    
    args = parser.parse_args()
    
    # Initialize scraper
    scraper = ProductScraper(headless=args.headless) 
    try:
        total_products = 0
        for website in args.urls:
            logger.info(f"===== Starting to process website: {website} =====")
            # search_and_scrape now returns the number of successfully scraped products
            product_count = scraper.search_and_scrape(
                website, 
                search_term=args.search,
                max_pages=args.max_pages, 
                max_products_per_page=args.max_products,
                scroll_speed=args.scroll_speed
            )
            total_products += product_count
            logger.info(f"===== Website: {website} processing complete, scraped {product_count} products =====")
        
        logger.info(f"All websites processed, total products scraped: {total_products}")
            
    finally:
        del scraper 