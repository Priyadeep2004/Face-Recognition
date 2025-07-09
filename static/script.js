const video = document.getElementById('videoElement');
const startButton = document.getElementById('startButton');
const stopButton = document.getElementById('stopButton');
const captureButton = document.getElementById('captureButton');
const statusMessage = document.getElementById('statusMessage');
const flashEffect = document.getElementById('flashEffect');
const canvas = document.getElementById('canvasElement');
const recognitionResultsDiv = document.getElementById('recognitionResults');
const loadingSpinner = document.getElementById('loadingSpinner');

let stream = null;
let currentResults = []; // Stores recognized attendance items for display
let attendanceChartInstance = null; // Chart.js instance

/**
 * Sets a status message with a given type and an optional auto-hide.
 * @param {string} message - The message to display.
 * @param {string} type - The type of message ('info', 'success', 'error').
 * @param {boolean} autoHide - Whether the message should hide automatically after a delay.
 */
function setStatusMessage(message, type = 'info', autoHide = true) {
  statusMessage.textContent = message;
  statusMessage.className = `status-message ${type}`; // Clear previous types and add new one
  statusMessage.classList.remove('hidden');

  if (autoHide) {
    setTimeout(() => {
      statusMessage.classList.add('hidden');
      statusMessage.textContent = ''; // Clear message content after hiding
    }, 5000); // Hide after 5 seconds
  }
}

// Function to update the recognition results display and chart
async function updateResultsDisplay() {
    try {
        const response = await fetch('/get_attendance_data');
        const data = await response.json();

        if (data.status === 'success') {
            // Update the text log
            recognitionResultsDiv.innerHTML = '<h3>Attendance Log</h3>';
            if (data.dates.length > 0) {
                // Fetch full attendance for list display, as /get_attendance_data is summarized
                const fullAttendanceResponse = await fetch('/attendance');
                const parser = new DOMParser();
                const htmlDoc = parser.parseFromString(await fullAttendanceResponse.text(), 'text/html');
                const tableRows = htmlDoc.querySelectorAll('.attendance-table tbody tr');
                
                if (tableRows.length > 0) {
                    const ul = document.createElement('ul');
                    ul.className = 'attendance-list';
                    // Limit to last 5 entries for a compact view on the dashboard
                    for (let i = 0; i < Math.min(tableRows.length, 5); i++) {
                        const name = tableRows[i].children[0].textContent;
                        const timestamp = tableRows[i].children[1].textContent;
                        const li = document.createElement('li');
                        li.textContent = `${name} at ${timestamp}`;
                        ul.appendChild(li);
                    }
                    recognitionResultsDiv.appendChild(ul);
                    if (tableRows.length > 5) {
                        const viewAllLink = document.createElement('p');
                        viewAllLink.innerHTML = `<a href="/attendance" class="view-all-link">View All Attendance</a>`;
                        recognitionResultsDiv.appendChild(viewAllLink);
                    }
                } else {
                    recognitionResultsDiv.innerHTML += '<p class="result-placeholder">No attendance recorded yet for you.</p>';
                }

                // Update the chart
                const ctx = document.getElementById('attendanceChart').getContext('2d');
                if (attendanceChartInstance) {
                    attendanceChartInstance.destroy(); // Destroy previous chart instance
                }
                attendanceChartInstance = new Chart(ctx, {
                    type: 'bar', // Bar chart for daily counts
                    data: {
                        labels: data.dates,
                        datasets: [{
                            label: 'Attendance Count',
                            data: data.counts,
                            backgroundColor: 'rgba(75, 192, 192, 0.6)',
                            borderColor: 'rgba(75, 192, 192, 1)',
                            borderWidth: 1
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        scales: {
                            y: {
                                beginAtZero: true,
                                title: {
                                    display: true,
                                    text: 'Number of Attendances'
                                },
                                ticks: {
                                    stepSize: 1, // Ensure integer ticks for counts
                                }
                            },
                            x: {
                                title: {
                                    display: true,
                                    text: 'Date'
                                }
                            }
                        },
                        plugins: {
                            title: {
                                display: true,
                                text: 'Your Daily Attendance'
                            },
                            legend: {
                                display: false
                            }
                        }
                    }
                });
            } else {
                recognitionResultsDiv.innerHTML += '<p class="result-placeholder">No attendance recorded yet for you.</p>';
                if (attendanceChartInstance) {
                    attendanceChartInstance.destroy(); // Destroy chart if no data
                    attendanceChartInstance = null;
                }
            }
        } else if (data.status === 'error') {
            recognitionResultsDiv.innerHTML = `<h3>Attendance Log</h3><p class="result-placeholder">${data.message}</p>`;
            if (attendanceChartInstance) {
                attendanceChartInstance.destroy();
                attendanceChartInstance = null;
            }
        }
    } catch (error) {
        console.error('Error fetching attendance data:', error);
        recognitionResultsDiv.innerHTML = '<h3>Attendance Log</h3><p class="result-placeholder">Failed to load attendance data.</p>';
        if (attendanceChartInstance) {
            attendanceChartInstance.destroy();
            attendanceChartInstance = null;
        }
    }
}


// Start Camera
startButton.addEventListener('click', async () => {
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: true });
    video.srcObject = stream;
    startButton.style.display = 'none';
    captureButton.style.display = 'inline-block';
    stopButton.style.display = 'inline-block';
    setStatusMessage('Camera started. Click "Capture Frame" to mark attendance.', 'info');
  } catch (err) {
    console.error('Error accessing camera: ', err);
    setStatusMessage('Error accessing camera. Please ensure camera is connected and permissions are granted.', 'error', false);
  }
});

// Stop Camera
stopButton.addEventListener('click', () => {
  if (stream) {
    stream.getTracks().forEach(track => track.stop());
    video.srcObject = null;
    stream = null;
    startButton.style.display = 'inline-block';
    captureButton.style.display = 'none';
    stopButton.style.display = 'none';
    setStatusMessage('Camera stopped.', 'info');
  }
});

// Capture Frame and Send for Recognition
captureButton.addEventListener('click', async () => {
  if (!stream) {
    setStatusMessage('Camera not started. Click "Start Camera" first.', 'error');
    return;
  }

  captureButton.disabled = true; // Disable button during processing
  loadingSpinner.classList.remove('hidden'); // Show spinner
  flashEffect.classList.add('active'); // Activate flash effect

  const context = canvas.getContext('2d');
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  const imageDataUrl = canvas.toDataURL('image/jpeg', 0.9); // Get image as JPEG data URL

  try {
    const response = await fetch('/process_face_recognition', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: `image_data=${encodeURIComponent(imageDataUrl)}`,
    });

    const result = await response.json();
    if (result.status === 'success') {
      setStatusMessage(result.message, 'success');
      // Update attendance log and chart on success
      updateResultsDisplay();
    } else if (result.status === 'info') {
      setStatusMessage(result.message, 'info');
    } else { // status is 'error'
      setStatusMessage(result.message, 'error');
    }
  } catch (error) {
    console.error('Network or processing error:', error);
    setStatusMessage(`Network error: ${error.message}. Please check server connection.`, 'error');
  } finally {
    // Always re-enable button and hide spinner/flash regardless of success or failure
    captureButton.disabled = false;
    loadingSpinner.classList.add('hidden');
    // Remove flash effect after a short delay
    setTimeout(() => {
        flashEffect.classList.remove('active');
    }, 150); 
  }
});

// Stop camera stream when the page is unloaded
window.addEventListener('beforeunload', () => {
  if (stream) {
    stream.getTracks().forEach(track => track.stop());
  }
});

// Initial setup when the DOM is fully loaded
document.addEventListener('DOMContentLoaded', () => {
    setStatusMessage("Click 'Start Camera' to begin face recognition!", 'info');
    updateResultsDisplay(); // Initialize chart and log display on page load

    // Adjust container margin-top for consistent layout with fixed navbar
    const container = document.querySelector('.container');
    if (container) {
        const navbarHeight = document.querySelector('.navbar').offsetHeight;
        container.style.marginTop = `${navbarHeight + 40}px`; // Add some extra margin
    }

    // Initialize AOS
    AOS.init({
        duration: 800,
        once: true, // Animation only happens once
        offset: 50,
    });
});