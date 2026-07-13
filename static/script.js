document.addEventListener('DOMContentLoaded', () => {
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const browseBtn = document.getElementById('browse-btn');
    const fileNameDisplay = document.getElementById('file-name');
    const optimizeBtn = document.getElementById('optimize-btn');
    const loader = document.getElementById('loader');
    const resultsSection = document.getElementById('results-section');
    
    const originalCodeEl = document.getElementById('original-code');
    const optimizedCodeEl = document.getElementById('optimized-code');
    const suggestionsContainer = document.getElementById('suggestions-container');

    let selectedFile = null;

    // Handle Drag and Drop
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, preventDefaults, false);
    });

    function preventDefaults(e) {
        e.preventDefault();
        e.stopPropagation();
    }

    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, highlight, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, unhighlight, false);
    });

    function highlight(e) {
        dropZone.classList.add('dragover');
    }

    function unhighlight(e) {
        dropZone.classList.remove('dragover');
    }

    dropZone.addEventListener('drop', handleDrop, false);

    function handleDrop(e) {
        const dt = e.dataTransfer;
        const files = dt.files;
        handleFiles(files);
    }

    // Handle Click to Browse
    browseBtn.addEventListener('click', () => {
        fileInput.click();
    });

    fileInput.addEventListener('change', function() {
        handleFiles(this.files);
    });

    function handleFiles(files) {
        if (files.length > 0) {
            const file = files[0];
            if (file.name.endsWith('.tf')) {
                selectedFile = file;
                fileNameDisplay.textContent = `Selected: ${file.name}`;
                optimizeBtn.disabled = false;
            } else {
                alert('Please select a valid Terraform (.tf) file.');
                selectedFile = null;
                fileNameDisplay.textContent = '';
                optimizeBtn.disabled = true;
            }
        }
    }

    // Handle Optimize API Call
    optimizeBtn.addEventListener('click', async () => {
        if (!selectedFile) return;

        const apiKey = document.getElementById('api-key').value.trim();
        if (!apiKey) {
            alert('Please enter your Groq API Key first.');
            return;
        }

        const formData = new FormData();
        formData.append('file', selectedFile);
        formData.append('api_key', apiKey);

        // UI State: Loading
        optimizeBtn.disabled = true;
        loader.classList.add('active');
        resultsSection.classList.add('hidden');
        resultsSection.classList.remove('active');

        try {
            const response = await fetch('/api/optimize', {
                method: 'POST',
                body: formData
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || 'Something went wrong processing the file.');
            }

            // Populate Data
            originalCodeEl.textContent = data.original_tf;
            optimizedCodeEl.textContent = data.new_terraform;
            renderSuggestions(data.results);

            // UI State: Success
            loader.classList.remove('active');
            resultsSection.classList.remove('hidden');
            
            // Scroll to results smoothly
            resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

        } catch (error) {
            alert(`Error: ${error.message}`);
            loader.classList.remove('active');
        } finally {
            optimizeBtn.disabled = false;
        }
    });

    function renderSuggestions(results) {
        suggestionsContainer.innerHTML = '';
        
        if (results.length === 0) {
            suggestionsContainer.innerHTML = '<p>No optimizable resources found in the provided file.</p>';
            return;
        }

        results.forEach(result => {
            const card = document.createElement('div');
            card.className = 'suggestion-card';
            
            let html = `<h4>Resource: <code>${result.original_name}</code></h4>`;
            html += `<p class="reason">"${result.reason}"</p>`;
            
            if (result.updated_resource) {
                html += `<p><strong>Suggested Type:</strong> ${result.updated_resource.resource_type}</p>`;
                
                // Provenance
                html += `<p style="margin-top: 1rem; font-weight: 600;">Data Sources:</p>`;
                html += `<ul class="provenance-list">`;
                
                for (const [field, source] of Object.entries(result.provenance)) {
                    if (source !== 'missing') {
                         let sourceDisplay = source;
                         if (source.startsWith('original:')) sourceDisplay = 'Carried over from original code';
                         else if (source.startsWith('template:')) sourceDisplay = `Copied from sibling resource (${source.split(':')[1]})`;
                         else if (source === 'llm') sourceDisplay = 'Generated by AI (Cost Optimization)';
                         
                         html += `<li><span class="field">${field}</span>: <span class="source">${sourceDisplay}</span></li>`;
                    }
                }
                html += `</ul>`;
            } else {
                html += `<p>No valid optimization generated.</p>`;
            }

            card.innerHTML = html;
            suggestionsContainer.appendChild(card);
        });
    }
});
