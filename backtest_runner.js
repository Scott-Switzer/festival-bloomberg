const { execSync } = require('child_process');

async function run() {
    console.log("Environment check: Searching for available runtimes...");
    try {
        const pythonVersion = execSync('python --version').toString().trim();
        console.log("Python version:", pythonVersion);
    } catch (e) {
        console.log("Python not found via 'python'. Checking 'python3'...");
        try {
            const python3Version = execSync('python3 --version').toString().trim();
            console.log("Python3 version:", python3Version);
        } catch (e3) {
            console.log("Python/Python3 not found in standard path.");
        }
    }
}

run();