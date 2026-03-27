document.addEventListener('DOMContentLoaded', function() {
    const form = document.querySelector('form[action*="contact:form"]');
    if (!form) return;

    const submitBtn = document.getElementById('submit-btn');
    const loadingDiv = document.querySelector('.loading');
    const errorDiv = document.querySelector('.error-message');
    const sentDiv = document.querySelector('.sent-message');

    form.addEventListener('submit', function(e) {
        e.preventDefault();

        // Clear previous messages
        errorDiv.style.display = 'none';
        sentDiv.style.display = 'none';
        errorDiv.innerHTML = '';

        // Show loading
        loadingDiv.style.display = 'block';

        // Disable submit button
        submitBtn.disabled = true;

        const formData = new FormData(form);

        fetch(form.action, {
            method: 'POST',
            body: formData,
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value
            }
        })
        .then(response => response.json())
        .then(data => {
            loadingDiv.style.display = 'none';
            submitBtn.disabled = false;

            if (data.status === 'success') {
                sentDiv.style.display = 'block';
                form.reset();
            } else {
                errorDiv.innerHTML = data.message;
                errorDiv.style.display = 'block';
            }
        })
        .catch(error => {
            loadingDiv.style.display = 'none';
            submitBtn.disabled = false;
            errorDiv.innerHTML = 'An error occurred. Please try again.';
            errorDiv.style.display = 'block';
        });
    });
});
