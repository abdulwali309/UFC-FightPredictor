# UFC Fight Predictor


<img width="566" height="259" alt="image" src="https://github.com/user-attachments/assets/e5c2a582-72f7-49f3-be14-0659dc84ee5e" />


In my and many others' opinion, MMA is the most unpredictable sport. With unexpected knockouts and so many intangibles like knockout power, it becomes very difficult to accurately predict a winner. My goal was to train a machine learning model as in-depth as possible to predict future fight outcomes. 

End-to-end pipeline for predicting UFC fight outcomes using fighter statistics, recent form, and physical attributes.**

This project is a complete UFC fight prediction system built entirely from scratch. It processes historical fight data, fighter statistics, and event information to create an end-to-end machine learning pipeline that predicts the probability of a fighter winning.

### Features

- **Data Cleaning & Merging:** Combines multiple CSV datasets (`fighters.csv`, `fighters_stats.csv`, `fights.csv`, `events.csv`) to create a comprehensive dataset with fighter physicals, career stats, and recent form.
- **Feature Engineering:** Generates core features including:
  - Career win rates
  - Differences in strikes, takedowns, knockdowns, submissions
  - Significant strike percentages
  - Physical differences (height, weight, reach)
  - Recent-form statistics with decay weighting
- **Categorical Encoding:** Handles fighter stance, fighting style, and weight class using one-hot encoding.
- **Train/Test Split:** Splits fights chronologically to prevent data leakage.
- **Machine Learning Model:** 
  - XGBoost classifier
  - Calibrated probabilities using isotonic regression
- **Evaluation:** Accuracy, ROC AUC, log loss, classification report, confusion matrix, and feature importance.
- **Interactive CLI:** 
  - Autocomplete for fighter names
  - Fuzzy matching using `rapidfuzz`
  - Predict probability of a fighter winning

### Results

- **Accuracy:** 78.91%  
- **ROC AUC:** 0.8675  
- **Log Loss:** 0.4547  

Top features contributing to predictions include: `SigStr_diff`, `STR_diff`, `recent_winrate_diff`, and various fighting style and weight class indicators.

### How to Run

Install dependencies:

```bash
pip install pandas numpy scikit-learn xgboost rapidfuzz prompt_toolkit joblib




Technologies & Libraries

Python 3

Data Handling: pandas, numpy

Machine Learning: scikit-learn, xgboost

Fuzzy Matching: rapidfuzz

Interactive CLI: prompt_toolkit

Model Persistence: joblib
