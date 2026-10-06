# Phase 9 Testing And Evaluation

Use `phase9_evaluation.py` to measure:
- File classification accuracy
- AI runtime vs manual organization time estimate
- Reduction in human mistakes (using either an assumed baseline or a manual baseline CSV)

## Required Input

1. A dataset folder with mixed files.
2. A ground truth CSV with columns:
- `file_path` (relative to dataset root)
- `expected_folder` (expected organizer folder path)

Template: `phase9_ground_truth_template.csv`

## Optional Manual Baseline

To compare against a real/manual sorting baseline, provide a CSV with:
- `file_path`
- `manual_folder`

Template: `phase9_manual_baseline_template.csv`

## Run

```bash
python3 phase9_evaluation.py <dataset_root> <ground_truth_csv> --pretty
```

With manual baseline and report exports:

```bash
python3 phase9_evaluation.py <dataset_root> <ground_truth_csv> \
  --manual-baseline-csv <manual_baseline_csv> \
  --manual-seconds-per-file 18 \
  --output-json phase9_results/evaluation.json \
  --output-report phase9_results/evaluation.md \
  --pretty
```

## Output

- `summary`: core metrics for accuracy, runtime, time saved, and mistakes reduced
- `confusion_matrix`: expected vs predicted folder counts
- `top_errors`: highest-confidence misclassifications to inspect first
- `files`: per-file evaluation details
