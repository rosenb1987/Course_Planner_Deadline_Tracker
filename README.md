# Course_Planner_Deadline_Tracker
Final Year Project – Course Planner &amp; Deadline Tracker (Flask web app)
HOW TO RUN THE COURSE PLANNER & DEADLINE TRACKER

Project Title:
Course Planner & Deadline Tracker for University Students

Student:
Rosen Bogomilov (w1804923)

Module:
6COSC023W – Final Year Project


------------------------------------------------------------
1. SOFTWARE REQUIREMENTS
------------------------------------------------------------

The following software is required:

• Python 3.10 or newer
• A web browser (Chrome, Edge, or Firefox recommended)

Optional but recommended:
• Visual Studio Code or another code editor
• Git (for downloading the project from GitHub)


------------------------------------------------------------
2. PROJECT STRUCTURE
------------------------------------------------------------

Make sure the entire project folder is extracted before running.

Important files and folders include:

- app.py              Main Flask application
- schema.sql          Database schema
- templates/          HTML templates
- static/             CSS and static files
- requirements.txt    Python dependencies
- HOW_TO_RUN.txt      This instruction file

Note:
The SQLite database file is automatically created when the
application runs if it does not already exist.


------------------------------------------------------------
3. INSTALL REQUIRED LIBRARIES
------------------------------------------------------------

Open Command Prompt or Terminal inside the project folder.


Then install dependencies:

    pip install -r requirements.txt

If pip is not recognised:

    python -m pip install -r requirements.txt


------------------------------------------------------------
4. RUNNING THE APPLICATION
------------------------------------------------------------

Inside the project folder, run:

    python app.py

or on some systems:

    python3 app.py

If successful, the terminal will display something like:

    Running on http://127.0.0.1:5000


------------------------------------------------------------
5. OPEN THE APPLICATION
------------------------------------------------------------

Open a web browser and go to:

    http://127.0.0.1:5000

The login page of the system should appear.


------------------------------------------------------------
6. USING THE SYSTEM
------------------------------------------------------------

Users can:

• Register and log in
• Add coursework tasks
• Edit tasks
• Delete tasks
• Automatically assign priority based on deadline
• Update task status (To do / In progress / Completed)
• View urgent tasks in the Attention section
• Filter tasks by module and priority
• Search tasks
• Track progress statistics
• View module progress bars
• Export tasks to CSV


------------------------------------------------------------
7. STOPPING THE APPLICATION
------------------------------------------------------------

To stop the server, return to the terminal window and press:

    CTRL + C


------------------------------------------------------------
8. NOTES
------------------------------------------------------------

• SQLite database requires no additional installation.
• Database file is created automatically if missing.
• Application runs locally and does not require internet access.
• The project is intended for demonstration and academic use.


End of Instructions
