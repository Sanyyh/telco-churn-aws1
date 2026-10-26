# Telco Customer Churn Prediction on AWS

An end-to-end churn prediction system on AWS. Upload a CSV of current customers (no `Churn` column) to S3 and, within about a minute, receive a CSV with each customer's churn probability, risk band and top reasons.

Dataset: [Telco Customer Churn](https://www.kaggle.com/datasets/blastchar/telco-customer-churn) (7,043 customers, 21 columns, 26.54% churn).

## How it works

```
TRAINING (once, in a SageMaker notebook)
  S3 raw/ -> notebooks (clean, EDA, train) -> S3 processed/, outputs/, outputs/models/v1/

SCORING (automatic, on every upload)
  you upload CSV -> S3 incoming/ -> S3 event -> Lambda (container image from ECR)
        -> validate -> score -> S3 outputs/scores/<name>_scored.csv + S3 logs/
                                      |
                              Athena (SQL on scores and customers)
```

| Service | Role |
| --- | --- |
| S3 (`telco-churn-aws-2026`, ap-southeast-2) | Data, model, scores and logs |
| SageMaker notebook (ml.t3.medium) | Cleaning, EDA, training |
| Lambda (zip) | Validates training files uploaded to `raw/` (needs the `Churn` column) |
| Lambda (container, x86_64, 2048 MB, 120 s) | Validates and scores new customer files |
| ECR (`churn-score`) | Stores the scoring container image |
| CloudWatch Logs | Lambda run output |
| Athena (`churn_db`) | SQL analytics on `customers` and `scores` tables |
| IAM | Least-privilege roles for Lambda and SageMaker |

## Results

| Model | Accuracy | Precision | Recall | F1 | ROC-AUC |
| --- | --- | --- | --- | --- | --- |
| **Logistic Regression** (selected) | 0.738 | 0.504 | 0.783 | 0.614 | 0.842 |
| Random Forest | 0.786 | 0.628 | 0.473 | 0.540 | 0.821 |

- Logistic Regression catches 293 of the 374 churners in the test set (Random Forest: 177), which matters most for retention.
- Risk bands: **High** >= 0.40, **Medium** 0.20 to 0.40, **Low** < 0.20. Across all 7,043 customers, actual churn was 47.2% in High, 14.1% in Medium and 3.0% in Low. This includes training data, so it is somewhat optimistic.
- The deployed pipeline reproduced the notebook scores exactly (max difference 0.0 on 200 customers, 100% band agreement).
- Probabilities use `class_weight="balanced"`, so they rank customers well but are not calibrated likelihoods.

## Repository layout

```
.
├── notebooks/
│   ├── 1-validation.ipynb              raw data checks, test files for the raw/ validator
│   ├── 2-EDA.ipynb                     data quality and churn analysis, figures
│   ├── 3-training_model.ipynb          split, preprocessing, both models, saved model
│   ├── 4-Testing.ipynb                 local batch scoring check
│   ├── 5-model_card.ipynb              writes model_card.json
│   ├── 06_thresholds_and_business.ipynb  cost-based threshold and sensitivity analysis
│   └── 07_pipeline_validation.ipynb    good-file and bad-file tests of the deployed pipeline
├── pipeline/
│   ├── handler.py                      scoring Lambda (validate, score, write results)
│   ├── Dockerfile                      container image for the Lambda
│   └── requirements.txt                pinned library versions
├── src/predict.py                      single-customer scoring function
├── sql/                                Athena table definitions and queries
├── docs/                               report and screenshots
└── README.md
```

Adjust the folder names above to match your repository.

## Running the project

### 1. Training (SageMaker notebook)
1. Put `Telco-Customer-Churn.csv` in `Dataset/`.
2. Run the notebooks in order: `2-EDA`, `3-training_model`, `5-model_card`, `4-Testing`, then `06_thresholds_and_business`.
3. This creates `outputs/best_churn_model.joblib`, `model_metrics.csv`, `churn_predictions.csv`, `customers_cleaned.csv`, `model_card.json` and `outputs/figures/`.
4. Upload the model and card to `s3://telco-churn-aws-2026/outputs/models/v1/`.

### 2. Deploy the scoring pipeline
1. Build the image from `pipeline/` (the Dockerfile uses the same Python and library versions as training) and push it to the ECR repository `churn-score`.
2. Create a container-image Lambda from that image. Use **x86_64** if the image was built on x86_64; a mismatch causes `Runtime.InvalidEntrypoint`. Set memory to 2048 MB and timeout to 120 s.
3. Give the Lambda role read/write access to the bucket, plus `AWSLambdaBasicExecutionRole`.
4. Add an S3 event notification: prefix `incoming/`, suffix `.csv`, destination the Lambda. Prefixes must not overlap with other triggers.
5. Optional environment variable `MODEL_KEY` (default `outputs/models/v1/best_churn_model.joblib`).

### 3. Score new customers
1. Prepare a CSV with the same columns as the training data, without `Churn`.
2. Upload it to `s3://telco-churn-aws-2026/incoming/`. The trigger fires only on new uploads, so use a new file name each time.
3. Download `outputs/scores/<name>_scored.csv`. A log for the run is written to `logs/`.

### 4. Validate the pipeline
Run `07_pipeline_validation.ipynb`. It creates four test files (two good, two bad). Upload them to `incoming/` by hand, download the logs and scored CSVs, put them in `pipeline_results/`, and run the remaining cells for a PASS/FAIL summary.

## Input checks

A file is rejected (FAILED log, no output) if:
- a required feature column is missing
- a numeric field is not a number (for example `tenure` = "abc")
- a category is not one seen in training (for example `Contract` = "Monthly")

Blank `TotalCharges` is repaired (set to 0 when `tenure` is 0) and logged as a warning.

## Output columns

`customerID`, `churn_probability`, `risk_band`, `reason_1` to `reason_3`, `model`, `model_version`, `scored_at`, `actual_churn` (empty for new customers).

## Requirements

Python with `scikit-learn==1.6.1`, `pandas==2.3.3`, `joblib==1.6.0`, `numpy`, `matplotlib`, `seaborn`, `statsmodels` (for the A/B power calculation) and `boto3`. The container image needs the same scikit-learn and pandas versions as training, otherwise the saved model may not load.

## Cost and cleanup

- Stop the SageMaker notebook instance when not in use and delete it after submission.
- A monthly AWS Budget alert is configured.
- Remove leftovers: `build/` prefix in S3, duplicate model files outside `outputs/models/v1/`, test files in `incoming/`, old ECR images, and `PowerUserAccess` from the Lambda role. Move `scores_demo.csv` out of `outputs/scores/`.

## Limitations and future work

- One dataset, one train/test split, batch scoring only.
- Cost assumptions in the threshold analysis ($30 offer, 30% success rate, 12 months retained revenue) are illustrative.
- Retention uplift is unproven until an A/B trial is run.
- Next steps: probability calibration, gradient boosting comparison, retraining with drift monitoring, Lambda error alarms and email notifications.

## Authors

-22CDS0434_Abilas
-22CDS0442_Thushan
-22CDS0405_Nasmath
-22CDS0438_Saneeh

