import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")

# Load CSVs
fighters = pd.read_csv("fighters.csv")
fighters_stats = pd.read_csv("fighters_stats.csv")
fights = pd.read_csv("fights.csv")
events = pd.read_csv("events.csv")


# Create target and Winner
# Make binary label from Result_1 ('W' -> Fighter_1 wins)
fights['Fighter_1_Win'] = fights['Result_1'].apply(lambda x: 1 if str(x).strip().upper() == 'W' else 0)

# Create readable Winner column (useful for recent-form and debugging)
fights['Winner'] = fights.apply(
    lambda r: r['Fighter_1'] if str(r['Result_1']).strip().upper() == 'W'
              else (r['Fighter_2'] if str(r['Result_2']).strip().upper() == 'W' else 'Draw'),
    axis=1
)

print(fights[['Fighter_1','Fighter_2','Result_1','Result_2','Fighter_1_Win','Winner']].head())




# Merge event date
events['Date'] = pd.to_datetime(events['Date'], errors='coerce')
fights = fights.merge(events[['Event_Id','Date']], on='Event_Id', how='left')
print("Fights with dates:", fights['Date'].notna().sum(), "of", len(fights))



# Merge physicals and fighters_stats (prefixed)
# Merge Fighter_1 physicals
fights_merged = fights.merge(
    fighters[['Full Name', 'Ht.', 'Wt.', 'Reach', 'Stance', 'W', 'L', 'D', 'Belt']].drop_duplicates(subset='Full Name'),
    left_on='Fighter_1', right_on='Full Name', how='left'
).rename(columns={'Ht.':'F1_Ht.', 'Wt.':'F1_Wt.', 'Reach':'F1_Reach', 'Stance':'F1_Stance',
                  'W':'F1_W', 'L':'F1_L', 'D':'F1_D', 'Belt':'F1_Belt'}).drop(columns=['Full Name'])

# Merge Fighter_2 physicals
fights_merged = fights_merged.merge(
    fighters[['Full Name', 'Ht.', 'Wt.', 'Reach', 'Stance', 'W', 'L', 'D', 'Belt']].drop_duplicates(subset='Full Name'),
    left_on='Fighter_2', right_on='Full Name', how='left'
).rename(columns={'Ht.':'F2_Ht.', 'Wt.':'F2_Wt.', 'Reach':'F2_Reach', 'Stance':'F2_Stance',
                  'W':'F2_W', 'L':'F2_L', 'D':'F2_D', 'Belt':'F2_Belt'}).drop(columns=['Full Name'])

# Prepare fighters_stats copies with prefix (Full Name is present in fighters_stats)
if 'Full Name' in fighters_stats.columns:
    fs1 = fighters_stats.add_prefix('F1_').rename(columns={'F1_Full Name':'Fighter_1'})
    fs2 = fighters_stats.add_prefix('F2_').rename(columns={'F2_Full Name':'Fighter_2'})
else:
    # fallback: prefix everything and hope merges on name will match some column
    fs1 = fighters_stats.add_prefix('F1_')
    fs2 = fighters_stats.add_prefix('F2_')

# Merge stats into fights_merged
fights_merged = fights_merged.merge(fs1, on='Fighter_1', how='left')
fights_merged = fights_merged.merge(fs2, on='Fighter_2', how='left')

print("After merges, shape:", fights_merged.shape)

# Cleanup duplicate columns after merges
def resolve_physicals_conflicts(df):
    """
    Keeps the '_y' version (from fighters_stats) and drops the redundant '_x' from fighters merge.
    """
    drop_cols = [c for c in df.columns if c.endswith('_x')]
    df = df.drop(columns=drop_cols)
    
    # Rename _y columns to their clean names
    df = df.rename(columns=lambda c: c.replace('_y', '') if c.endswith('_y') else c)
    
    return df

fights_merged = resolve_physicals_conflicts(fights_merged)

print("Cleaned up duplicate physical columns")
print([c for c in fights_merged.columns if c.startswith('F1_') or c.startswith('F2_')][:20])






# Impute missing physicals (weight-class aware if possible)
# Determine weight class source: fights_merged may have Weight_Class per fight, or fighters_stats has Weight_Class per fighter
# We'll use F1_Weight_Class / F2_Weight_Class if present, else fights' Weight_Class (the fight-level column)
if 'F1_Weight_Class' in fights_merged.columns:
    wcol1 = 'F1_Weight_Class'
    wcol2 = 'F2_Weight_Class'
elif 'Weight_Class' in fights_merged.columns:
    wcol1 = wcol2 = 'Weight_Class'
else:
    wcol1 = wcol2 = None

# Function to impute column by grouping on weight class when available
def impute_by_weight_or_global(df, col_f1, col_f2, weight_col):
    # global means
    global_mean_f1 = df[col_f1].mean()
    global_mean_f2 = df[col_f2].mean()
    if weight_col:
        # fill with group means then global mean
        df[col_f1] = df.groupby(weight_col)[col_f1].transform(lambda g: g.fillna(g.mean()))
        df[col_f2] = df.groupby(weight_col)[col_f2].transform(lambda g: g.fillna(g.mean()))
    # fallback to global mean
    df[col_f1] = df[col_f1].fillna(global_mean_f1)
    df[col_f2] = df[col_f2].fillna(global_mean_f2)
    return df

fights_merged = impute_by_weight_or_global(fights_merged, 'F1_Reach', 'F2_Reach', wcol1)
fights_merged = impute_by_weight_or_global(fights_merged, 'F1_Ht.', 'F2_Ht.', wcol1)
fights_merged = impute_by_weight_or_global(fights_merged, 'F1_Wt.', 'F2_Wt.', wcol1)


# Core features
# 6a) Career win rates: prefer F1_W/F1_L from fighters_stats prefixed columns if present, else fallback to F1_W from fighters
def compute_winrate(row, wcol, lcol, dcol=None):
    w = row.get(wcol, np.nan)
    l = row.get(lcol, np.nan)
    d = row.get(dcol, 0) if dcol else 0
    if pd.notna(w) and pd.notna(l):
        denom = (w + l + (d if not pd.isna(d) else 0))
        return w / denom if denom > 0 else 0.0
    return np.nan

fights_merged['F1_WinRate'] = fights_merged.apply(lambda r: compute_winrate(r, 'F1_W', 'F1_L', 'F1_D'), axis=1)
fights_merged['F2_WinRate'] = fights_merged.apply(lambda r: compute_winrate(r, 'F2_W', 'F2_L', 'F2_D'), axis=1)

# fallback to fighters W/L columns if still NaN
fights_merged['F1_WinRate'] = fights_merged['F1_WinRate'].fillna(fights_merged['F1_W'] / ((fights_merged['F1_W'] + fights_merged['F1_L']).replace(0,np.nan))).fillna(0)
fights_merged['F2_WinRate'] = fights_merged['F2_WinRate'].fillna(fights_merged['F2_W'] / ((fights_merged['F2_W'] + fights_merged['F2_L']).replace(0,np.nan))).fillna(0)

# 6b) Basic diffs STR/TD/KD/SUB using fight-level STR_1 / STR_2 etc. or prefixed F1_/F2_ columns
for stat in ['STR','TD','KD','SUB']:
    c1 = stat + '_1'
    c2 = stat + '_2'
    pref1 = 'F1_' + stat
    pref2 = 'F2_' + stat
    if c1 in fights_merged.columns and c2 in fights_merged.columns:
        fights_merged[f'{stat}_diff'] = fights_merged[c1].fillna(0) - fights_merged[c2].fillna(0)
        fights_merged[f'{stat}_1'] = fights_merged[c1].fillna(0)
        fights_merged[f'{stat}_2'] = fights_merged[c2].fillna(0)
    elif pref1 in fights_merged.columns and pref2 in fights_merged.columns:
        fights_merged[f'{stat}_diff'] = fights_merged[pref1].fillna(0) - fights_merged[pref2].fillna(0)
        fights_merged[f'{stat}_1'] = fights_merged[pref1].fillna(0)
        fights_merged[f'{stat}_2'] = fights_merged[pref2].fillna(0)
    else:
        fights_merged[f'{stat}_diff'] = 0
        fights_merged[f'{stat}_1'] = 0
        fights_merged[f'{stat}_2'] = 0

# 6c) Significant strike % differences (check several possible column names)
if 'Sig. Str. %_1' in fights_merged.columns and 'Sig. Str. %_2' in fights_merged.columns:
    fights_merged['SigStr_diff'] = fights_merged['Sig. Str. %_1'].fillna(0) - fights_merged['Sig. Str. %_2'].fillna(0)
elif 'F1_Sig. Str. %' in fights_merged.columns and 'F2_Sig. Str. %' in fights_merged.columns:
    fights_merged['SigStr_diff'] = fights_merged['F1_Sig. Str. %'].fillna(0) - fights_merged['F2_Sig. Str. %'].fillna(0)
else:
    fights_merged['SigStr_diff'] = 0

# 6d) physical diffs (already imputed)
fights_merged['Reach_diff'] = fights_merged['F1_Reach'] - fights_merged['F2_Reach']
fights_merged['Ht._diff'] = fights_merged['F1_Ht.'] - fights_merged['F2_Ht.']
fights_merged['Wt._diff'] = fights_merged['F1_Wt.'] - fights_merged['F2_Wt.']

# 6e) WinRate diff
fights_merged['WinRate_diff'] = fights_merged['F1_WinRate'] - fights_merged['F2_WinRate']

print("Core features created. Sample:")
print(fights_merged[['F1_WinRate','F2_WinRate','WinRate_diff','STR_diff','TD_diff','KD_diff','SUB_diff','Reach_diff']].head())







# Recent form
metric_cols_for_recent = ['STR','KD','TD','SUB']  # stats to aggregate from previous fights
N_recent = 3
decay_weights = [0.6, 0.3, 0.1]

# Ensure Winner column exists (we created it earlier from Result_1/Result_2)
if 'Winner' not in fights_merged.columns:
    fights_merged['Winner'] = fights_merged.apply(lambda r: r['Fighter_1'] if r['Fighter_1_Win']==1 else r['Fighter_2'], axis=1)

# create recent columns with default 0
for fighter_col in ['Fighter_1','Fighter_2']:
    fights_merged[f'{fighter_col}_recent_winrate'] = 0.0
    for m in metric_cols_for_recent:
        fights_merged[f'{fighter_col}_recent_{m}'] = 0.0

# helper function reused (same as earlier)
def compute_recent_metrics(row, fights_df, fighter_col, metric_cols, N=3, decay_weights=None):
    fighter_name = row[fighter_col]
    fight_date = row['Date']
    hist_mask = ( (fights_df['Fighter_1'] == fighter_name) | (fights_df['Fighter_2'] == fighter_name) )
    if 'Date' in fights_df.columns and pd.notna(fight_date):
        hist_mask = hist_mask & (fights_df['Date'] < fight_date)
    hist = fights_df[hist_mask].sort_values('Date', ascending=False)
    if len(hist) == 0:
        return {f'{fighter_col}_recent_winrate': 0.0, **{f'{fighter_col}_recent_{m}':0.0 for m in metric_cols}}
    hist_last = hist.head(N)
    recent_winrate = (hist_last['Winner'] == fighter_name).sum() / len(hist_last)
    metric_vals = {}
    for m in metric_cols:
        vals = []
        for _, hr in hist_last.iterrows():
            if hr['Fighter_1'] == fighter_name:
                colname = m + '_1' if (m + '_1') in fights_df.columns else ('F1_' + m if ('F1_'+m) in fights_df.columns else None)
            else:
                colname = m + '_2' if (m + '_2') in fights_df.columns else ('F2_' + m if ('F2_'+m) in fights_df.columns else None)
            if colname and colname in hr.index:
                vals.append(hr[colname] if pd.notna(hr[colname]) else 0.0)
            else:
                vals.append(0.0)
        if decay_weights is None:
            metric_vals[f'{fighter_col}_recent_{m}'] = np.mean(vals) if len(vals)>0 else 0.0
        else:
            w = decay_weights[:len(vals)]
            metric_vals[f'{fighter_col}_recent_{m}'] = np.average(vals, weights=w)
    d = {f'{fighter_col}_recent_winrate': recent_winrate}
    d.update(metric_vals)
    return d

print("Computing recent-form features")
for idx, row in fights_merged.sort_values('Date').iterrows():
    r1 = compute_recent_metrics(row, fights_merged, 'Fighter_1', metric_cols_for_recent, N=N_recent, decay_weights=decay_weights)
    r2 = compute_recent_metrics(row, fights_merged, 'Fighter_2', metric_cols_for_recent, N=N_recent, decay_weights=decay_weights)
    for k,v in r1.items():
        fights_merged.at[idx, k] = v
    for k,v in r2.items():
        fights_merged.at[idx, k] = v

# recent diffs
for m in metric_cols_for_recent:
    fights_merged[f'{m}_recent_diff'] = fights_merged[f'Fighter_1_recent_{m}'] - fights_merged[f'Fighter_2_recent_{m}']
fights_merged['recent_winrate_diff'] = fights_merged['Fighter_1_recent_winrate'] - fights_merged['Fighter_2_recent_winrate']

print("Recent-form features done. Sample:")
print(fights_merged[['Fighter_1_recent_winrate','Fighter_2_recent_winrate','recent_winrate_diff']].head())





# Final features & missing handling
feature_cols = []

# diffs
for c in ['STR_diff','TD_diff','KD_diff','SUB_diff','SigStr_diff','WinRate_diff']:
    if c in fights_merged.columns:
        feature_cols.append(c)

# physical diffs
for c in ['Ht._diff','Wt._diff','Reach_diff']:
    if c in fights_merged.columns:
        feature_cols.append(c)

# recent diffs
for m in metric_cols_for_recent:
    col = f'{m}_recent_diff'
    if col in fights_merged.columns:
        feature_cols.append(col)
if 'recent_winrate_diff' in fights_merged.columns:
    feature_cols.append('recent_winrate_diff')

# add raw corner winrates
for c in ['F1_WinRate','F2_WinRate']:
    if c in fights_merged.columns:
        feature_cols.append(c)

# include some raw side stats if present
for base in ['STR_1','STR_2','TD_1','TD_2','KD_1','KD_2','SUB_1','SUB_2']:
    if base in fights_merged.columns:
        feature_cols.append(base)

# dedupe and ensure exists
feature_cols = [c for i,c in enumerate(dict.fromkeys(feature_cols)) if c in fights_merged.columns]
print("Using features (count):", len(feature_cols))

# missing treatment: numeric medians
for c in feature_cols:
    if fights_merged[c].dtype.kind in 'biufc':
        med = fights_merged[c].median()
        fights_merged[c].fillna(med, inplace=True)
    else:
        fights_merged[c].fillna(0, inplace=True)

# categorical encoding
cat_cols = []
for c in ['F1_Stance','F2_Stance','F1_Fighting Style','F2_Fighting Style','Weight_Class']:
    if c in fights_merged.columns:
        cat_cols.append(c)

print("Categorical columns:", cat_cols)
if len(cat_cols)>0:
    fights_merged = pd.get_dummies(fights_merged, columns=cat_cols, dummy_na=False, drop_first=True)
    # add dummies to feature list
    dummies = [c for c in fights_merged.columns if any(orig in c for orig in cat_cols)]
    feature_cols += dummies

# final feature cols dedupe
feature_cols = [c for i,c in enumerate(dict.fromkeys(feature_cols)) if c in fights_merged.columns]
print("Final feature count:", len(feature_cols))




# Train/test split
from sklearn.model_selection import train_test_split

if 'Date' in fights_merged.columns and fights_merged['Date'].notna().sum() > 0:
    fights_sorted = fights_merged.sort_values('Date').reset_index(drop=True)
    cutoff_date = fights_sorted['Date'].quantile(0.8)
    train_df = fights_sorted[fights_sorted['Date'] <= cutoff_date].copy()
    test_df  = fights_sorted[fights_sorted['Date'] > cutoff_date].copy()
    print("Time split:", cutoff_date, "train/test sizes:", train_df.shape[0], test_df.shape[0])
else:
    train_df, test_df = train_test_split(fights_merged, test_size=0.2, random_state=42)
    print("Random split used. train/test sizes:", train_df.shape[0], test_df.shape[0])

X_train = train_df[feature_cols]
y_train = train_df['Fighter_1_Win']
X_test = test_df[feature_cols]
y_test = test_df['Fighter_1_Win']



# Preprocessing categorical columns for XGBoost

# Find categorical columns (dtype = object)
cat_cols = X_train.select_dtypes(include=['object']).columns

# Apply label encoding for each categorical column
from sklearn.preprocessing import LabelEncoder

for col in cat_cols:
    le = LabelEncoder()
    # Fit on training data, transform both train and test
    X_train[col] = le.fit_transform(X_train[col].astype(str))
    X_test[col] = le.transform(X_test[col].astype(str))
print(X_train.dtypes)






# Train + calibrate + evaluate
from xgboost import XGBClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import accuracy_score, roc_auc_score, log_loss, classification_report, confusion_matrix

model = XGBClassifier(n_estimators=300, learning_rate=0.05, max_depth=5,
                      use_label_encoder=False, eval_metric='logloss',
                      random_state=42, n_jobs=-1)
model.fit(X_train, y_train)

# Calibrate probabilities (CV)
calibrator = CalibratedClassifierCV(model, method='isotonic', cv=3)
calibrator.fit(X_train, y_train)

# Predict & metrics
y_pred = calibrator.predict(X_test)
y_prob = calibrator.predict_proba(X_test)[:,1]

print("\n=== Evaluation ===")
print("Accuracy:", round(accuracy_score(y_test, y_pred),4))
try:
    print("ROC AUC:", round(roc_auc_score(y_test, y_prob),4))
except:
    print("ROC AUC: could not compute")
print("Log Loss:", round(log_loss(y_test, y_prob),4))
print("\nClassification report:\n", classification_report(y_test, y_pred))
print("Confusion matrix:\n", confusion_matrix(y_test, y_pred))

# top features via XGBoost importance
try:
    importances = pd.Series(model.feature_importances_, index=X_train.columns).sort_values(ascending=False)
    print("\nTop features:\n", importances.head(20))
except Exception as e:
    print("Feature importance error:", e)




# Save model and helpers
import joblib
joblib.dump({'model': model, 'calibrator': calibrator, 'features': feature_cols}, "ufc_model_bundle.joblib")

# helper to build features for new match (F1 is red corner)
def build_features_for_match(f1_name, f2_name, fights_df=fights_merged, fighters_df=fighters_stats, fighters_phys=fighters, feature_list=feature_cols, N_recent=3):
    # create row with zeros
    row = pd.Series(index=feature_list, dtype=float).fillna(0.0)

    # safe_get_winrate (tries fighters_stats then fighters)
    def safe_get_winrate(name):
        try:
            r = fighters_stats[fighters_stats['Full Name']==name].iloc[0]
            w = r.get('W', 0); l = r.get('L', 0); d = r.get('D', 0)
            denom = (w + l + (d if not pd.isna(d) else 0)) or 1
            return w / denom
        except:
            try:
                r = fighters[fighters['Full Name']==name].iloc[0]
                w = r.get('W', 0); l = r.get('L', 0); d = r.get('D', 0)
                denom = (w + l + (d if not pd.isna(d) else 0)) or 1
                return w / denom
            except:
                return 0.0

    row['F1_WinRate'] = safe_get_winrate(f1_name)
    row['F2_WinRate'] = safe_get_winrate(f2_name)
    if 'WinRate_diff' in feature_list:
        row['WinRate_diff'] = row['F1_WinRate'] - row['F2_WinRate']

    # physicals
    def get_phys(name):
        try:
            r = fighters[fighters['Full Name']==name].iloc[0]
            return {'Ht.': r.get('Ht.', np.nan), 'Wt.': r.get('Wt.', np.nan), 'Reach': r.get('Reach', np.nan)}
        except:
            return {'Ht.':np.nan,'Wt.':np.nan,'Reach':np.nan}
    p1 = get_phys(f1_name); p2 = get_phys(f2_name)
    if 'Ht._diff' in feature_list:
        row['Ht._diff'] = (p1['Ht.'] if not pd.isna(p1['Ht.']) else 0) - (p2['Ht.'] if not pd.isna(p2['Ht.']) else 0)
    if 'Wt._diff' in feature_list:
        row['Wt._diff'] = (p1['Wt.'] if not pd.isna(p1['Wt.']) else 0) - (p2['Wt.'] if not pd.isna(p2['Wt.']) else 0)
    if 'Reach_diff' in feature_list:
        row['Reach_diff'] = (p1['Reach'] if not pd.isna(p1['Reach']) else 0) - (p2['Reach'] if not pd.isna(p2['Reach']) else 0)

    # recent-form using compute_recent_metrics with Date now
    now_row = pd.Series({'Date': pd.Timestamp.now(), 'Fighter_1': f1_name, 'Fighter_2': f2_name})
    recent1 = compute_recent_metrics(now_row, fights_df, 'Fighter_1', metric_cols_for_recent, N=N_recent, decay_weights=decay_weights)
    recent2 = compute_recent_metrics(now_row, fights_df, 'Fighter_2', metric_cols_for_recent, N=N_recent, decay_weights=decay_weights)
    for m in metric_cols_for_recent:
        fname = f'{m}_recent_diff'
        if fname in feature_list:
            row[fname] = recent1.get('Fighter_1_recent_'+m,0.0) - recent2.get('Fighter_2_recent_'+m,0.0)
    if 'recent_winrate_diff' in feature_list:
        row['recent_winrate_diff'] = recent1.get('Fighter_1_recent_winrate',0.0) - recent2.get('Fighter_2_recent_winrate',0.0)

    # career stat fallbacks (STR,TD,KD,SUB)
    for stat in ['STR','TD','KD','SUB']:
        col1 = stat + '_1'; col2 = stat + '_2'
        if col1 in feature_list or col2 in feature_list:
            def get_stat(name, s):
                try:
                    r = fighters_stats[fighters_stats['Full Name']==name].iloc[0]
                    return r.get(s, 0.0) if pd.notna(r.get(s, np.nan)) else 0.0
                except:
                    return 0.0
            if col1 in feature_list:
                row[col1] = get_stat(f1_name, stat)
            if col2 in feature_list:
                row[col2] = get_stat(f2_name, stat)

    # ensure all feature columns exist
    for c in feature_list:
        if c not in row.index:
            row[c] = 0.0

    return pd.DataFrame([row[feature_list]])

# predict helper
def predict_match(f1_name, f2_name, model_bundle_path="ufc_model_bundle.joblib"):
    bundle = joblib.load(model_bundle_path)
    model = bundle['model']
    calibrator = bundle['calibrator']
    features = bundle['features']
    Xnew = build_features_for_match(f1_name, f2_name, fights_df=fights_merged,
                                    fighters_df=fighters_stats, fighters_phys=fighters,
                                    feature_list=features, N_recent=N_recent)
    prob = calibrator.predict_proba(Xnew)[:,1][0]
    return prob, Xnew


# Imports
from rapidfuzz import process
from prompt_toolkit import prompt
from prompt_toolkit.completion import WordCompleter

# Prepare fighter list
all_fighters = sorted(set(fighters['Full Name'].dropna().unique()))

# setup autocomplete
fighter_completer = WordCompleter(all_fighters, ignore_case=True, match_middle=True)

# Fuzzy name resolver
def find_closest_fighter_name(name):
    """Finds closest match to user input using fuzzy matching."""
    match, score, _ = process.extractOne(name, all_fighters)
    if score > 70:
        return match
    else:
        return None

# Interactive loop
def interactive_predict():
    print("\n=== UFC Fight Predictor ===")
    print("Type 'quit' at any time to exit.\n")

    while True:
        # input fighter 1 (autocomplete)
        f1_input = prompt("Enter Fighter 1 (red corner / favorite): ", completer=fighter_completer)
        if f1_input.lower() == "quit":
            print("Exiting predictor...")
            break

        f2_input = prompt("Enter Fighter 2 (blue corner): ", completer=fighter_completer)
        if f2_input.lower() == "quit":
            print("Exiting predictor...")
            break

        # fuzzy match
        f1_name = find_closest_fighter_name(f1_input)
        f2_name = find_closest_fighter_name(f2_input)

        if not f1_name or not f2_name:
            print(" One or both fighter names not found. Try again.\n")
            continue

        print(f"\nMatched fighters: 🟥 {f1_name}  vs  🟦 {f2_name}")

        # make prediction
        try:
            prob, Xnew = predict_match(f1_name, f2_name)
            print(f"→ Probability {f1_name} wins: {prob:.2%}\n")
        except Exception as e:
            print(f" Prediction failed: {e}\n")

# Run
interactive_predict()