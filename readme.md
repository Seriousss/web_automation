
```bash
conda create -n web-auto python=3.12 -y
conda activate web-auto
pip install -r requirements.txt
export DEEPSEEK_API_KEY= YOUR_API_KEY

# Task 1
# Example Usage
python src/university.py --url "https://cse.engin.umich.edu/people/faculty/" --output "umich.json"

# Results can be found under university_output folder. (I also ran the script on the faculty pages of UW and UIUC.)


#Task 2
# Run the scraper with single website
python src/shopping.py --urls https://www.walmart.com/ --search "PlayStation"

# Run with multiple websites
python src/shopping.py --urls https://www.walmart.com/ https://www.target.com/ --search "PlayStation"

# Results can be found under shopping_output

# Noting that items may be duplicated on each page, run the following command to deduplicate the JSONL file.
python src/deduplicate_jsonl.py shopping_output/www.walmart.com_products.jsonl
```

