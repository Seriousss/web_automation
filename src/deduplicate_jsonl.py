#!/usr/bin/env python3


import os
import json
import argparse
import sys
from collections import Counter


def deduplicate_jsonl_file(input_file, output_file=None, key_field='url', verbose=True):
    """
    Deduplicate a JSONL file based on specified key field
    
    Args:
        input_file: Path to the input JSONL file
        output_file: Path to the output JSONL file (if None, will use input_file + ".deduplicated.jsonl")
        key_field: Field to use for deduplication (default: 'url')
        verbose: Whether to print progress information
        
    Returns:
        tuple: (Number of input records, Number of output records, Number of duplicates removed)
    """
    if not os.path.exists(input_file):
        if verbose:
            print(f"Error: Input file not found: {input_file}")
        return 0, 0, 0
    
    if not output_file:
        # If no output file specified, use input filename with .deduplicated.jsonl suffix
        base, ext = os.path.splitext(input_file)
        output_file = f"{base}_deduplicated{ext}"
    
    if verbose:
        print(f"Reading from: {input_file}")
        print(f"Writing to:   {output_file}")
        print(f"Deduplicating based on '{key_field}' field...", flush=True)
    
    try:
        # Read all records
        records = []
        with open(input_file, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                try:
                    line = line.strip()
                    if not line:  # Skip empty lines
                        continue
                    record = json.loads(line)
                    records.append(record)
                except json.JSONDecodeError as e:
                    if verbose:
                        print(f"Warning: Invalid JSON on line {line_num}: {e}")
                        print(f"Line content: {line[:70]}...")
        
        if verbose:
            print(f"Read {len(records)} records from input file")
        
        # Count occurrences of each key value
        key_values = [record.get(key_field) for record in records if record.get(key_field)]
        key_counts = Counter(key_values)
        
        # Identify duplicates
        duplicate_keys = [k for k, v in key_counts.items() if v > 1]
        if verbose and duplicate_keys:
            print(f"Found {len(duplicate_keys)} keys with duplicates")
            # Print top duplicates for information
            top_duplicates = sorted([(k, v) for k, v in key_counts.items() if v > 1], 
                                   key=lambda x: x[1], reverse=True)[:5]        
        # Deduplicate records
        unique_records = {}
        for record in records:
            key = record.get(key_field)
            if key and key not in unique_records:
                unique_records[key] = record
        
        # Write unique records to output file
        with open(output_file, "w", encoding="utf-8") as f:
            for i, record in enumerate(unique_records.values()):
                if i > 0:
                    f.write("\n")
                json.dump(record, f, ensure_ascii=False)
        
        num_input = len(records)
        num_output = len(unique_records)
        num_duplicates = num_input - num_output
        
        if verbose:
            print(f"\nDeduplication complete:")
            print(f"  Input records:     {num_input}")
            print(f"  Output records:    {num_output}")
            print(f"  Duplicates removed: {num_duplicates}")
            
        return num_input, num_output, num_duplicates
        
    except Exception as e:
        if verbose:
            print(f"Error during deduplication: {e}")
        return 0, 0, 0


def main():
    parser = argparse.ArgumentParser(description="Deduplicate JSONL files based on a specified field")
    parser.add_argument("input_file", help="Input JSONL file to deduplicate")
    parser.add_argument("-o", "--output", help="Output file path (default: input_file_deduplicated.jsonl)")
    parser.add_argument("-k", "--key", default="url", help="Field to use as unique key (default: url)")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress progress output")
    
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)
    
    args = parser.parse_args()
    
    deduplicate_jsonl_file(args.input_file, args.output, args.key, not args.quiet)


if __name__ == "__main__":
    main() 