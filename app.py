from flask import Flask, render_template, request, jsonify
import os
from werkzeug.utils import secure_filename
from optimizer import optimize_entire_tf_file

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB limit

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/optimize', methods=['POST'])
def optimize():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    if file and file.filename.endswith('.tf'):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        try:
            result = optimize_entire_tf_file(filepath)
            
            # Read original tf to send back
            with open(filepath, 'r') as f:
                original_tf = f.read()
                
            return jsonify({
                'original_tf': original_tf,
                'results': result['results'],
                'new_terraform': result['new_terraform']
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500
        finally:
            # Clean up uploaded file
            if os.path.exists(filepath):
                os.remove(filepath)
    else:
        return jsonify({'error': 'Invalid file format. Please upload a .tf file.'}), 400

if __name__ == '__main__':
    app.run(debug=True, port=5000)
