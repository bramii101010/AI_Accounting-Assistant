import os
import pandas as pd
import numpy as np
import re
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
import io
import base64
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime
import json

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['UPLOAD_FOLDER'] = 'uploads'
app.secret_key = 'your-secret-key-change-this-in-production'

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

class PIIDetector:
    """Detects and redacts Personally Identifiable Information"""
    
    @staticmethod
    def detect_ssn(text):
        """Detect Social Security Numbers"""
        if pd.isna(text):
            return False
        text = str(text)
        # SSN patterns: XXX-XX-XXXX or XXXXXXXXX
        ssn_pattern = r'\b\d{3}-?\d{2}-?\d{4}\b'
        return bool(re.search(ssn_pattern, text))
    
    @staticmethod
    def detect_phone(text):
        """Detect phone numbers"""
        if pd.isna(text):
            return False
        text = str(text)
        # Phone patterns: (XXX) XXX-XXXX, XXX-XXX-XXXX, etc.
        phone_pattern = r'\b(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}\b'
        return bool(re.search(phone_pattern, text))
    
    @staticmethod
    def detect_email(text):
        """Detect email addresses"""
        if pd.isna(text):
            return False
        text = str(text)
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        return bool(re.search(email_pattern, text))
    
    @staticmethod
    def detect_account_number(text):
        """Detect potential account numbers (8+ consecutive digits)"""
        if pd.isna(text):
            return False
        text = str(text)
        # Look for long sequences of digits that might be account numbers
        account_pattern = r'\b\d{8,}\b'
        return bool(re.search(account_pattern, text))
    
    @staticmethod
    def redact_pii(text, pii_type):
        """Redact PII based on type"""
        if pd.isna(text):
            return text
        
        text = str(text)
        if pii_type == 'ssn':
            return re.sub(r'\b\d{3}-?\d{2}-?\d{4}\b', 'XXX-XX-XXXX', text)
        elif pii_type == 'phone':
            return re.sub(r'\b(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}\b', 'XXX-XXX-XXXX', text)
        elif pii_type == 'email':
            return re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', 'REDACTED@EMAIL.COM', text)
        elif pii_type == 'account':
            return re.sub(r'\b\d{8,}\b', lambda m: 'X' * len(m.group()), text)
        return text

class AnomalyDetector:
    """Detects anomalies in accounting data"""
    
    @staticmethod
    def detect_duplicate_payments(df):
        """Detect potential duplicate payments"""
        anomalies = []
        if 'amount' in df.columns and 'vendor' in df.columns:
            # Find duplicate amounts to same vendor on same day (if date available)
            duplicates = df.groupby(['vendor', 'amount']).size()
            duplicates = duplicates[duplicates > 1]
            
            for (vendor, amount), count in duplicates.items():
                anomalies.append({
                    'type': 'Duplicate Payment',
                    'description': f'${amount:,.2f} payment to {vendor} appears {count} times',
                    'severity': 'Medium',
                    'records_affected': count
                })
        return anomalies
    
    @staticmethod
    def detect_amount_outliers(df):
        """Detect unusually large or small amounts"""
        anomalies = []
        if 'amount' in df.columns:
            amounts = pd.to_numeric(df['amount'], errors='coerce').dropna()
            
            if len(amounts) > 0:
                Q1 = amounts.quantile(0.25)
                Q3 = amounts.quantile(0.75)
                IQR = Q3 - Q1
                lower_bound = Q1 - 1.5 * IQR
                upper_bound = Q3 + 1.5 * IQR
                
                outliers = amounts[(amounts < lower_bound) | (amounts > upper_bound)]
                
                for amount in outliers:
                    severity = 'High' if abs(amount) > amounts.median() * 10 else 'Medium'
                    anomalies.append({
                        'type': 'Amount Outlier',
                        'description': f'Unusual amount: ${amount:,.2f}',
                        'severity': severity,
                        'records_affected': 1
                    })
        return anomalies
    
    @staticmethod
    def detect_negative_amounts(df):
        """Detect negative amounts that might be suspicious"""
        anomalies = []
        if 'amount' in df.columns:
            amounts = pd.to_numeric(df['amount'], errors='coerce')
            negative_amounts = amounts[amounts < 0].dropna()
            
            for amount in negative_amounts:
                anomalies.append({
                    'type': 'Negative Amount',
                    'description': f'Negative transaction: ${amount:,.2f}',
                    'severity': 'Medium',
                    'records_affected': 1
                })
        return anomalies

def analyze_data(df):
    """Main function to analyze uploaded data"""
    results = {
        'pii_detected': {},
        'pii_count': 0,
        'anomalies': [],
        'summary_stats': {},
        'redacted_data': df.copy(),
        'charts': {}
    }
    
    # Detect PII
    pii_detector = PIIDetector()
    pii_found = False
    
    for column in df.columns:
        column_pii = []
        for index, value in df[column].items():
            if pii_detector.detect_ssn(value):
                column_pii.append('SSN')
                df.loc[index, column] = pii_detector.redact_pii(value, 'ssn')
                pii_found = True
            elif pii_detector.detect_phone(value):
                column_pii.append('Phone')
                df.loc[index, column] = pii_detector.redact_pii(value, 'phone')
                pii_found = True
            elif pii_detector.detect_email(value):
                column_pii.append('Email')
                df.loc[index, column] = pii_detector.redact_pii(value, 'email')
                pii_found = True
            elif pii_detector.detect_account_number(value):
                column_pii.append('Account Number')
                df.loc[index, column] = pii_detector.redact_pii(value, 'account')
                pii_found = True
        
        if column_pii:
            results['pii_detected'][column] = list(set(column_pii))
            results['pii_count'] += len(column_pii)
    
    results['redacted_data'] = df
    
    # Detect anomalies
    anomaly_detector = AnomalyDetector()
    results['anomalies'].extend(anomaly_detector.detect_duplicate_payments(df))
    results['anomalies'].extend(anomaly_detector.detect_amount_outliers(df))
    results['anomalies'].extend(anomaly_detector.detect_negative_amounts(df))
    
    # Generate summary statistics
    if 'amount' in df.columns:
        amounts = pd.to_numeric(df['amount'], errors='coerce').dropna()
        if len(amounts) > 0:
            results['summary_stats'] = {
                'total_transactions': len(df),
                'total_amount': amounts.sum(),
                'average_amount': amounts.mean(),
                'median_amount': amounts.median(),
                'max_amount': amounts.max(),
                'min_amount': amounts.min()
            }
    
    return results

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if file and file.filename.endswith('.csv'):
        try:
            # Read CSV file
            df = pd.read_csv(file)
            
            # Analyze the data
            results = analyze_data(df)
            
            # Convert DataFrames to HTML for display
            original_html = results['redacted_data'].head(20).to_html(classes='table table-striped', table_id='original-table')
            
            # Create charts if amount column exists
            chart_url = None
            if 'amount' in df.columns:
                amounts = pd.to_numeric(df['amount'], errors='coerce').dropna()
                if len(amounts) > 0:
                    plt.figure(figsize=(10, 6))
                    plt.hist(amounts, bins=20, alpha=0.7, color='skyblue', edgecolor='black')
                    plt.title('Distribution of Transaction Amounts')
                    plt.xlabel('Amount ($)')
                    plt.ylabel('Frequency')
                    plt.grid(True, alpha=0.3)
                    
                    # Save plot to base64 string
                    img_buffer = io.BytesIO()
                    plt.savefig(img_buffer, format='png', bbox_inches='tight')
                    img_buffer.seek(0)
                    chart_url = base64.b64encode(img_buffer.getvalue()).decode()
                    plt.close()
            
            return jsonify({
                'success': True,
                'pii_detected': results['pii_detected'],
                'pii_count': results['pii_count'],
                'anomalies': results['anomalies'],
                'summary_stats': results['summary_stats'],
                'table_html': original_html,
                'chart_url': chart_url,
                'total_rows': len(df)
            })
            
        except Exception as e:
            return jsonify({'error': f'Error processing file: {str(e)}'}), 500
    
    return jsonify({'error': 'Please upload a CSV file'}), 400

if __name__ == '__main__':
    app.run(debug=True, port=5000)
