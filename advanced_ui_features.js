// advanced_ui_features.js

// Comprehensive UI Interaction Features

// Function to show notifications
function showNotification(message) {
    const notification = document.createElement('div');
    notification.className = 'notification';
    notification.innerText = message;
    document.body.appendChild(notification);
    setTimeout(() => {
        notification.remove();
    }, 3000);
}

// Function to create modals
function createModal(title, content) {
    const modal = document.createElement('div');
    modal.className = 'modal';
    const modalContent = `<h2>${title}</h2><p>${content}</p><button onclick='closeModal(this)'>Close</button>`;
    modal.innerHTML = modalContent;
    document.body.appendChild(modal);
}

// Close modal function
function closeModal(button) {
    const modal = button.closest('.modal');
    modal.remove();
}

// Function to show tooltips
function showTooltip(element, message) {
    const tooltip = document.createElement('div');
    tooltip.className = 'tooltip';
    tooltip.innerText = message;
    element.appendChild(tooltip);
    tooltip.style.display = 'block';
    setTimeout(() => {
        tooltip.remove();
    }, 2000);
}

// Function to animate elements
function animateElement(element, animation) {
    element.classList.add(animation);
    element.addEventListener('animationend', () => {
        element.classList.remove(animation);
    });
}

// Example usage:
// showNotification('Welcome to Mahabbas Game!');
// createModal('Game Info', 'This is a fun and interactive game.');
// showTooltip(document.getElementById('example'), 'This is an example tooltip.');
// animateElement(document.getElementById('animateMe'), 'fade-in');