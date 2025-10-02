# Newborn Danger Checker

A prototype Streamlit web app that helps health workers and mothers assess potential danger signs in newborns. The app uses a trained machine learning model to evaluate maternal, newborn, and household data and predict the risk level of health complications, including congenital heart disease (CHD).

## Features

* 📋 **Data Input Form**: Collects maternal, newborn, and household details.
* 🤖 **ML Model Integration**: Runs a trained classifier to predict newborn risk.
* ⚠️ **Risk Levels**: Returns results as *Low*, *Moderate*, or *High Risk*.
* 🩺 **Health Guidance**: Provides a recommendation to seek medical evaluation for moderate/high risk cases.
* 📊 **Results Logging**: Option to save predictions for monitoring.
* 🌍 **Scalable**: Can be extended to include geospatial mapping for regional health dashboards.

## Project Goals

This app was designed as part of a maternal and child health project to demonstrate how AI and data science can support early detection of newborn risks in Kenya and beyond.

## Tech Stack

* **Python**
* **Streamlit** (for the web app interface)
* **scikit-learn / XGBoost** (for ML model)
* **pandas, numpy** (for data handling)

## Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/your-username/newborn-danger-checker.git
   cd newborn-danger-checker
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Run the app:

   ```bash
   streamlit run app.py
   ```

## Usage

1. Launch the app locally.
2. Enter newborn and maternal details in the form.
3. Click **Check Risk**.
4. View the prediction (Low/Moderate/High Risk).

## Future Improvements

* Integration with real-world health datasets.
* Geospatial dashboard for regional CHD risk mapping.
* Support for mobile-first design for field workers.
* Multi-language support (English, Swahili).

## Disclaimer

This prototype is for **educational and demonstration purposes only**. It should not replace professional medical advice or diagnosis.
