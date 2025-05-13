import os
import time
import json
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from collections import Counter
from openai import OpenAI
from collections import defaultdict
import argparse
import re
from selenium.webdriver.support.ui import WebDriverWait


client = OpenAI(
    base_url="https://api.deepseek.com/v1",
    api_key=os.getenv("DEEPSEEK_API_KEY")
)


parser = argparse.ArgumentParser(
    description="Profile scraper: specify URL to scrape and output file to write results."
)
parser.add_argument(
    "--url", required=True,
    help="Target directory page URL to scrape"
)
parser.add_argument(
    "--output", default="output.json",
    help="Path to output JSON file"
)
args = parser.parse_args()



def click_load_more(driver, max_clicks=10, pause=1):
    """
    Click any "load" buttons or links up to max_clicks times, pausing after each.
    """
    for i in range(1, max_clicks+1):
        xpath = (
            "//button"
            "[contains("
              "translate(normalize-space(.),"
                        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
                        "'abcdefghijklmnopqrstuvwxyz'),"
              "'load'"
            ")]"
            "|"
            "//a"
            "[contains("
              "translate(normalize-space(.),"
                        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
                        "'abcdefghijklmnopqrstuvwxyz'),"
              "'load'"
            ")]"
        )
        elems = driver.find_elements(By.XPATH, xpath)
        if not elems:
            # print(f"[DEBUG] no more load controls after {i-1} clicks")
            break

        btn = None
        for e in elems:
            if e.is_displayed() and e.is_enabled():
                btn = e
                break
        if not btn:
            print("[DEBUG] found load controls but none clickable, stopping")
            break

        text = btn.text.strip().replace("\n", " ")
        # print(f"[DEBUG] clicking load control #{i}: <{btn.tag_name}> '{text[:30]}…'")
        try:
            btn.click()
        except Exception as ex:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(0.5)
            btn.click()

        time.sleep(pause)

KEYWORDS = ["professor", "lecturer"]

def inspect_frequent_combos(driver, min_freq=5, top_n=10) -> list[str]:
    """
    Find the CSS class combo most enriched for professor/lecturer entries.
    """
    # 1) Find all elements with a class attribute
    elems = driver.find_elements(By.CSS_SELECTOR, "*[class]")
    combos = []
    for e in elems:
        cls = (e.get_attribute("class") or "").strip()
        if cls:
            combos.append(cls)
    cnt = Counter(combos)

    # 2) Sort by frequency and truncate
    freq_list = [
        (combo, freq) 
        for combo, freq in cnt.most_common() 
        
        if freq >= min_freq
    ][:top_n]
    # for combo, freq in freq_list:
    #     print(f"  » '{combo}'  freq={freq}")

    scored = []
    for combo, freq in freq_list:
        xpath = f"//*[@class={repr(combo)}]"
        nodes = driver.find_elements(By.XPATH, xpath)

        total = len(nodes)
        hit   = sum(1 for n in nodes
                    if any(kw in (n.text or "").lower() for kw in KEYWORDS))
        ratio = hit / total if total else 0
        # print(f"[DEBUG] combo='{combo}' | hits={hit}/{total} | ratio={ratio:.2f}")
        scored.append((combo, ratio))

    if not scored:
        print("[DEBUG] no combos scored → returning []")
        return []

    best_combo, best_ratio = max(scored, key=lambda x: x[1])
    # print(f"\n[DEBUG] best combo by 'professor|lecturer' ratio: '{best_combo}' (ratio={best_ratio:.2f})")
    return [best_combo]



def parse_rules_json(rules_json: str) -> dict:
    """Extract JSON object from LLM response."""
    m = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        rules_json,
        flags=re.DOTALL
    )
    if m:
        json_str = m.group(1)
    else:
        start = rules_json.find("{")
        end   = rules_json.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("No JSON object found in rules_json")
        json_str = rules_json[start : end+1]

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON: {e}\nJSON was:\n{json_str}")


def scrape_faculty(url, output_path):
    driver = uc.Chrome(version_main=135)

    driver.get(url)
    time.sleep(15)  # wait for JS/AJAX to load
    click_load_more(driver, max_clicks=12, pause=3)
    common = inspect_frequent_combos(driver, min_freq=50, top_n=5)
    nodes = driver.find_elements(By.CSS_SELECTOR, f"[class='{common[0]}']")
    # Sample card to infer rules
    sample_html = nodes[1].get_attribute("innerHTML")
    # print(f"[DEBUG] sample HTML: {sample_html}")
    prompt = f"""
    Here is an HTML snippet for a single profile card (inside a <div class="{common[0]}">):
    {sample_html}
    Please analyze this and produce a JSON object with exactly these seven keys: "name", "title", "email", "research interest".  
    For each key, provide:
    1. "selector": a CSS selector that selects the element containing the field. Please be concise. I should be able to find the element by using this seletor in CSS_SELECTOR.
    2. "tag_pattern": the exact opening and closing tag (including the class attribute) between which the field value lives, e.g. "<span class='field field--name-title ...'>" and "</span>".  
    if you think a field is not present, set the selected value to an empty string.
    Only return the JSON object, nothing else.
    """

    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": "You are an expert at inferring data extraction rules from HTML."},
            {"role": "user",   "content": prompt.strip()},
        ],
        temperature=0,
        max_tokens=500,
    )

    rules_json = resp.choices[0].message.content
    rules = parse_rules_json(rules_json)
    rules = {
        field: info
        for field, info in rules.items()
        if info.get("selector", "").strip()  
    }
    # print(f"[DEBUG] extracted rules: {json.dumps(rules, indent=2, ensure_ascii=False)}")
    cards = driver.find_elements(By.CSS_SELECTOR, f"[class='{common[0]}']")

    extracted = []
    for card in cards:
        item = {}
        html = card.get_attribute("innerHTML")

        for field, rule in rules.items():
            sel = rule.get("selector","").strip()
            if sel:
                try:
                    el = card.find_element(By.CSS_SELECTOR, sel)
                    item[field] = el.text.strip()
                    if not el.text.strip():
                        parent = el.find_element(By.XPATH, "..")
                        val = parent.get_attribute("data-value") or ""
                        item[field] = val
                    continue
                except Exception:
                    # print(f"[DEBUG] selector '{sel}' failed to find element in card")
                    pass


            item[field] = ""
        extracted.append(item)
    unique = []
    seen = set()
    for item in extracted:
        # use sorted items tuple as hashable key
        key = tuple(sorted(item.items()))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    extracted = unique

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(extracted, f, indent=2, ensure_ascii=False)
        print(f"Saved extraction to {output_path}")
    driver.quit()
        

if __name__ == "__main__":
    data = scrape_faculty(args.url, args.output)
