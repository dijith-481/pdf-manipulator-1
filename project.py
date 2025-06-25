import os
import uuid
from flask import Flask, send_file, request, render_template, jsonify, session
from flask_session import Session
from werkzeug.utils import secure_filename
import fitz
from flask_apscheduler import APScheduler
import time
import base64
import mimetypes
from zipfile import ZipFile
import shutil


app = Flask(__name__)
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)


scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()
# creates temp folder
app.config["TEMP_FOLDER"] = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "temp"
)
if not os.path.exists(app.config["TEMP_FOLDER"]):
    os.makedirs(app.config["TEMP_FOLDER"])

    # timeout 15min
TEMP_FILE_TIMEOUT = 900


# main page
@app.route("/")
def index():
    print("started")
    return render_template("index.html")


# handles upload
@app.route("/upload", methods=["post"])
def upload():
    try:
        uploaded_files = []
        for file_key in request.files:
            if file_key not in request.files:
                return jsonify({"success": False, "error": f"no {file_key}part"})
            file = request.files[file_key]
            if file.filename == "":
                return jsonify(
                    {"success": False, "error": f"No selected file for {file_key}"}
                )
            if not file.filename.endswith(".pdf"):
                return jsonify(
                    {"success": False, "error": f"Not a PDF file for {file_key}"}
                )
            temp_filename = generate_temp_filename(
                file.filename
            )  # create secure file name
            temp_filepath = os.path.join(app.config["TEMP_FOLDER"], temp_filename)
            try:
                file.save(temp_filepath)

                uploaded_files.append(temp_filename)
                try:
                    pages = pdfDetails(temp_filepath)
                except:
                    None
            except:
                return jsonify({"success": False, "error": "Error saving file"})

        store_session_data(uploaded_files)  # stores session data

        return jsonify({"success": True, "pageNo": pages})  # retrurns details of pdf
    except:
        return jsonify({"success": False, "error": "error while recieving file"})


# generate temporary filename
def generate_temp_filename(filename):
    extension = os.path.splitext(filename)[1]
    return secure_filename(str(uuid.uuid4())) + extension


# generate temporary folder name
def generate_temp_folder():
    return secure_filename(str(uuid.uuid4()))


# send file directly
@app.route("/downloadsendfile/<filename>", methods=["get"])
def download_fileSendAsFile(filename):
    if os.path.isdir(os.path.join(app.config["TEMP_FOLDER"], filename)):
        directory_path = os.path.join(app.config["TEMP_FOLDER"], filename)
        if not os.path.isdir(directory_path):
            return "directory not found", 404

        if os.path.exists(directory_path):
            directory_path = zip_directory(directory_path)
            return send_file(
                directory_path,
                as_attachment=True,
                download_name=f"download.zip",
                mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        else:
            return "error whle sending file"

    else:
        file_path = os.path.join(app.config["TEMP_FOLDER"], filename)
        extension = os.path.splitext(filename)[1]
        if os.path.exists(file_path):
            return send_file(
                file_path,
                as_attachment=True,
                download_name=f"download.{extension}",
                mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        else:
            return "error", 404


# handles the action choosed
@app.route("/process/<action>", methods=["post"])
def action(action):
    filename = session["files"][0]  # get file name from session
    inputFilePath = os.path.join(app.config["TEMP_FOLDER"], filename)
    if action == "image":  # handles if action is image
        outputFileName = generate_temp_folder()
        os.makedirs(os.path.join(app.config["TEMP_FOLDER"], outputFileName))
    elif action == "merge":
        filenames = session["files"]
        inputFilePath = []
        for filename in filenames:
            inputFilePath.append(os.path.join(app.config["TEMP_FOLDER"], filename))
        outputFileName = generate_temp_filename("merge.pdf")
    else:  # handles other actions
        outputFileName = generate_temp_filename("split.pdf")
    outputFilePath = os.path.join(app.config["TEMP_FOLDER"], outputFileName)
    # to call functions dynamically
    actions = {
        "split": split_pdf,
        "encrypt": encrypt_pdf,
        "decrypt": decrypt_pdf,
        "compress": compress_pdf,
        "addText": watermark_pdf,
        "image": image_pdf,
        "merge": merge_pdf,
    }
    option = request.form.get("type")  # recieve if available
    value = request.form.get("value")  # recieve if available
    password = request.form.get("password")  # recieve if available

    try:
        err = actions[action](
            inputFile=inputFilePath,
            outputFile=outputFilePath,
            **({"option": option} if option else {}),
            **({"value": value} if value else {}),
            **({"password": password} if password else {}),
        )
        # call function
        if err["success"] == False:
            return jsonify({"success": False, "error": err["error"]})
        if action == "compress":
            compress_percent = getCompressPercent(
                inputFilePath, outputFilePath
            )  # handles special case of action
            url = "/downloadsendfile/" + outputFileName
            return jsonify(
                {"success": True, "url": url, "compress_percent": compress_percent}
            )
        else:
            sendingFile = download_file(
                outputFileName, action
            )  # function to send file as embedded
            return jsonify(
                {
                    "success": True,
                    "file": sendingFile,
                    "compress_percent": compress_percent
                    if action == "compress"
                    else None,
                }
            )
    except:
        return jsonify({"success": False, "error": "error while processing"})


# @app.route('/download/<filename>', methods=['get'])
# handles the sending of file as embedded
def download_file(filename, action):
    if os.path.isdir(
        os.path.join(app.config["TEMP_FOLDER"], filename)
    ):  # handles if directory
        directory_path = os.path.join(app.config["TEMP_FOLDER"], filename)
        if not os.path.isdir(directory_path):
            return ({"success": False, "error": "Directory not found"},)
        files_data = []
        try:
            for filenames in os.listdir(
                directory_path
            ):  # create a dictnory with each files
                file_path = os.path.join(directory_path, filenames)
                if os.path.isfile(file_path):
                    try:
                        with open(file_path, "rb") as f:
                            file_data = f.read()
                        encoded_data = base64.b64encode(file_data).decode("utf-8")
                        files_data.append(
                            {
                                "filename": filenames,
                                "file_data": encoded_data,
                                "mime_type": get_mime_type(filenames),
                            }
                        )
                    except Exception as e:
                        # Log the error and continue with the next file
                        # print(f"Error reading file {filenames}: {str(e)}")
                        return ({"success": False, "error": f"{str(file_path)}"},)

            if not files_data:
                return ({"success": False, "error": "No files found in the directory"},)
            try:
                return {
                    "success": True,
                    "directory_name": filename,
                    "files": files_data,
                }
            except:
                return ({"success": False, "error": "Directory found"},)
        except:
            return ({"success": False, "error": "Directory not found"},)
    else:  # handles if file
        file_path = os.path.join(app.config["TEMP_FOLDER"], filename)
        try:
            with open(file_path, "rb") as f:
                file_data = f.read()
            encoded_data = base64.b64encode(file_data).decode("utf-8")
            return {
                "success": True,
                "filename": f"{action}.pdf",
                "file_data": encoded_data,
                "mime_type": get_mime_type(filename),  # Adjust based on your file type
            }

        except Exception as e:
            return ({"sucess": False, "error": filename},)


# merge
def merge_pdf(inputFile, outputFile):
    """
    Merges multiple PDF files into a single PDF file.
    Args:
        inputFile: A list of paths to the input PDF files.
        outputFile: The path to the output PDF file.
    """
    try:
        output_pdf = fitz.open()  # Create a new PDF object to store the merged content
        for eachFile in inputFile:
            input_pdf = fitz.open(eachFile)  # Open each input PDF file
            output_pdf.insert_pdf(
                input_pdf
            )  # Insert the content of the input PDF into the output PDF
            input_pdf.close()  # Close the input PDF file
        output_pdf.save(outputFile)  # Save the merged PDF to the specified output file
        output_pdf.close()  # Close the output PDF object
        return {"success": True, "message": "done"}

    except:
        return {"success": False, "error": "Fail Merging"}


def split_pdf(inputFile, outputFile, option, value=None):
    """
    Splits a PDF file into multiple PDF files.

    Args:
        inputFile: The path to the input PDF file.
        outputFile: The path to the output PDF file.
        option: The option for splitting the PDF file.
        value: The value for the splitting option.
    """

    try:
        input_pdf = fitz.open(inputFile)  # Open the input PDF file
        totalPages = len(
            input_pdf
        )  # Get the total number of pages in the input PDF file
        splitPagesList = getPagesList(
            option, totalPages, value
        )  # Get the list of pages to split
        output_pdf = fitz.open()  # Create a new PDF object to store the split pages
        for pageNo in splitPagesList:  # Iterate over the list of pages to split
            output_pdf.insert_pdf(
                input_pdf, from_page=pageNo, to_page=pageNo
            )  # Insert the page into the output PDF file

        output_pdf.save(outputFile)  # Save the output PDF file
        input_pdf.close()  # Close the input PDF file
        output_pdf.close()  # Close the output PDF file
        return {
            "success": True,
            "message": "done",
        }  # Return 'done' if the splitting was successful
    except:
        return {
            "success": False,
            "error": "Failed splitting",
        }  # Return 'failed splitting' if the splitting failed


def getPagesList(Type, totalPages, Value=None):
    """
    Generates a list of page numbers based on the given type and total pages.

    Args:
        Type: A character indicating the type of page selection.
            'a': All pages.
            'm': First half of the pages.
            'f': Second half of the pages.
            'o': Odd-numbered pages.
            'e': Even-numbered pages.
            'c': Custom pages specified by Value.
        totalPages: The total number of pages.
        Value: A list of custom page numbers (optional, used only when Type is 'c').

    Returns:
        A list of page numbers or 'failed retrieving page list' if an error occurs.
    """
    try:
        PagesList = []  # Initialize an empty list to store page numbers
        if Type == "a":  # Select all pages
            for i in range(totalPages):
                PagesList.append(i)
        if Type == "m":  # Select the first half of the pages
            for i in range(totalPages // 2):
                PagesList.append(i)
        elif Type == "f":  # Select the second half of the pages
            for i in range(totalPages // 2, totalPages):
                PagesList.append(i)
        elif Type == "o":  # Select odd-numbered pages
            for i in range(0, totalPages, 2):
                PagesList.append(i)
        elif Type == "e":  # Select even-numbered pages
            for i in range(1, totalPages, 2):
                PagesList.append(i)
        elif Type == "c":  # Select custom pages from Value
            parts = Value.split(",")
            for part in parts:
                if "-" in part:
                    start_str, end_str = part.split("-")
                    start = int(start_str)
                    end = int(end_str)
                    PagesList.extend(range(start - 1, end))
                else:
                    page_num = int(part)
                    PagesList.append(page_num - 1)
        return PagesList  # Return the list of page numbers
    except:
        return "failed retrieving page list"  # Return an error message if something goes wrong


def encrypt_pdf(inputFile, outputFile, password):
    """
    Encrypts a PDF file.

    Args:
        inputFile: Path to the input PDF file.
        outputFile: Path to the encrypted output PDF file.
        password: Password to encrypt the PDF with.

    Returns:
        A string indicating whether the file was encrypted successfully or an error occurred.
    """
    try:
        # Open the input PDF file
        input_pdf = fitz.open(inputFile)

        # Set the encryption method to AES-256
        encrypt_meth = fitz.PDF_ENCRYPT_AES_256

        # Set permissions to allow accessibility, printing, and copying
        perm = int(
            fitz.PDF_PERM_ACCESSIBILITY | fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY
        )

        # Save the encrypted PDF file
        input_pdf.save(
            outputFile, encryption=encrypt_meth, user_pw=password, permissions=perm
        )

        # Close the PDF file
        input_pdf.close()

        # Return a success message
        return {"success": True, "message": "done"}

    except:
        # Return an error message if encryption fails
        return {"success": False, "error": "Fail Encrypting"}


def decrypt_pdf(inputFile, outputFile, password):
    """
    Decrypts a PDF file.

    Args:
        inputFile: Path to the input PDF file.
        outputFile: Path to the decrypted output PDF file.
        password: Password to decrypt the PDF with.

    Returns:
        A string indicating whether the file was decrypted successfully or an error occurred.
    """
    try:
        # Open the input PDF file
        input_pdf = fitz.open(inputFile)

        # Check if the PDF file is encrypted
        if input_pdf.is_encrypted:
            # Try to authenticate with the provided password
            if input_pdf.authenticate(password):
                # Save the decrypted PDF file
                input_pdf.save(outputFile)
                # Close the PDF file
                input_pdf.close()

            else:
                # Return an error message if the password doesn't match
                return {"success": False, "error": "Password Mismatched"}
        # Return a success message if the file is not encrypted
        return {"success": True, "message": "done"}
    except:
        # Return an error message if decryption fails
        return {"success": False, "error": "Fail Decrypting"}


def compress_pdf(inputFile, outputFile, value):
    """
    Compresses a PDF file.

    Args:
        inputFile: Path to the input PDF file.
        outputFile: Path to the compressed output PDF file.
        value: Compression level ('l' for low, 'h' for high, or any other value for medium).

    Returns:
        A string indicating whether the file was compressed successfully or an error occurred.
    """
    input_pdf = fitz.open(inputFile)  # Open the input PDF file

    # Set compression parameters based on the provided value
    if value == "l":
        params = {
            "deflate": True,  # Use deflate compression
            "clean": True,  # Clean the PDF file
            "garbage": 1,  # Garbage collection level 1
            "linear": True,  # Linearize the PDF file for faster web viewing
        }
    elif value == "h":
        params = {
            "deflate": True,  # Use deflate compression
            "clean": True,  # Clean the PDF file
            "garbage": 1,  # Garbage collection level 1
            "linear": True,  # Linearize the PDF file for faster web viewing
            "pretty": False,  # Disable pretty printing
            "ascii": False,  # Disable ASCII encoding
        }
    elif value == "m":
        params = {
            "deflate": True,  # Use deflate compression
            "clean": True,  # Clean the PDF file
            "garbage": 2,  # Garbage collection level 2
            "linear": True,  # Linearize the PDF file for faster web viewing
        }

    input_pdf.save(outputFile, **params)  # Save the compressed PDF file

    try:
        input_pdf.close()  # Close the input PDF file

        return {"success": True, "message": "done"}  # Return success message
    except:
        return {"success": False, "error": "compression Failed"}  # Return error message


def getCompressPercent(inputFile, outputFile):
    """
    Calculates the compression percentage between two files.

    Args:
        inputFile: Path to the original file.
        outputFile: Path to the compressed file.

    Returns:
        A string representing the compression percentage, formatted to two decimal places.
    """
    original_size = os.path.getsize(inputFile)  # Get the size of the original file
    compressed_size = os.path.getsize(outputFile)  # Get the size of the compressed file
    compression_percent = (
        1 - compressed_size / original_size
    ) * 100  # Calculate the compression percentage
    return f"{compression_percent:.2f}%"  # Return the formatted compression percentage


def watermark_pdf(inputFile, outputFile, value, option):
    """
    Adds a header or footer to each page of a PDF file.

    Args:
        inputFile: Path to the input PDF file.
        outputFile: Path to save the watermarked PDF file.
        value: The text or page number to use as the header or footer.
               If '<pg>' is provided, the page number will be used.
        option: Specifies the position of the text (e.g., 'tl' for top-left).

    Returns:
        A string indicating success or an error message.
    """
    try:
        input_pdf = fitz.open(inputFile)  # Open the input PDF file
        for i, page in enumerate(input_pdf):  # Iterate over each page
            if value == "<pg>":
                text = str(i + 1)  # Use page number as watermark
            else:
                text = value  # Use provided value as watermark
            rect = page.rect  # Get page dimensions
            fontsize = 12
            x, y = getPos(
                option, rect.width, rect.height
            )  # Calculate watermark position
            p = fitz.Point(x, y)
            page.insert_text(
                p,  # Position
                text,
                fontsize=fontsize,
                color=(0, 0, 0),  # Black color
                rotate=0,
            )
        input_pdf.save(outputFile)  # Save the watermarked PDF
        input_pdf.close()
        return {"success": True, "message": "done"}  # Indicate success
    except:
        return {
            "success": False,
            "error": "Fail adding text",
        }  # Indicate an error occurred


def image_pdf(inputFile, outputFile, option, value=None):
    """
    Converts specific pages of a PDF file to images.

    Args:
        inputFile: The path to the input PDF file.
        outputFile: The directory to save the images.
        option: The option for selecting pages (e.g., 'all', 'range', 'specific').
        value: Additional value for the option (e.g., page range or comma-separated page numbers).

    Returns:
        A string indicating success or an error message.
    """
    try:
        input_pdf = fitz.open(inputFile)  # Open the input PDF file
        totalPages = len(input_pdf)  # Get the total number of pages

        if value:
            value = value.split(",")  # Split the value if it's a comma-separated string

        imagePagesList = getPagesList(
            option, totalPages, value
        )  # Get the list of pages to convert

        for pageNo in imagePagesList:
            page = input_pdf[pageNo]  # Get the page object
            pix = page.get_pixmap()  # Render the page as a pixmap

            pix.save(
                f"{outputFile}/page {pageNo + 1}.png"
            )  # Save the pixmap as an image

        input_pdf.close()  # Close the PDF file
        return {"success": True, "message": "done"}  # Return success message
    except:
        return {"success": False, "error": "Fail converting"}  # Return error message


def getPos(pos, w, h):
    """
    Calculates the position coordinates based on the given position string and dimensions.

    Args:
        pos: A string indicating the position ('tl' for top-left, 'tr' for top-right,
             'bl' for bottom-left, 'br' for bottom-right).
        w: The width of the area.
        h: The height of the area.

    Returns:
        A tuple containing the (x, y) coordinates of the calculated position.
    """
    if pos == "tl":  # Top-left position
        return w / 8, h / 20
    elif pos == "tr":  # Top-right position
        return 7 * w / 8, h / 20
    elif pos == "bl":  # Bottom-left position
        return w / 8, 19 * h / 20
    elif pos == "br":  # Bottom-right position
        return 7 * w / 8, 19 * h / 20


def cleanup_temp_files():
    """Deletes temporary files that have exceeded the timeout."""
    now = time.time()
    for filename in os.listdir(app.config["TEMP_FOLDER"]):
        file_path = os.path.join(app.config["TEMP_FOLDER"], filename)
        if (
            os.path.isfile(file_path)
            and (now - os.path.getmtime(file_path)) > TEMP_FILE_TIMEOUT
        ):
            os.remove(file_path)
        elif (
            os.path.isdir(file_path)
            and (now - os.path.getmtime(file_path)) > TEMP_FILE_TIMEOUT
        ):
            shutil.rmtree(file_path)


def store_session_data(filenames):
    """
    Stores the filenames in the session.
    """
    session["files"] = []
    for filename in filenames:
        session["files"].append(filename)


def pdfDetails(pdf):
    """
    Returns the number of pages, author, and subject of a PDF file.
    """
    doc = fitz.open(pdf)

    page_count = len(doc)
    doc.close()
    return page_count


def get_mime_type(filename):
    """
    Returns the MIME type of a file based on its extension.
    """
    mimetypes.init()
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type or "application/octet-stream"


def zip_directory(directory):
    """
    Creates a ZIP file from a directory.
    """
    zip_filename = generate_temp_filename("zip.zip")
    zip_filepath = os.path.join(app.config["TEMP_FOLDER"], zip_filename)

    file_paths = []
    try:
        for root, dirs, files in os.walk(directory):
            for filename in files:
                file_path = os.path.join(root, filename)
                file_paths.append(file_path)
        with ZipFile(zip_filepath, "w") as zip:
            for file in file_paths:
                zip.write(file)
        return zip_filepath
    except:
        return "faeli"


@scheduler.task("interval", id="do_job_1", seconds=900, misfire_grace_time=300)
def job1():
    cleanup_temp_files()  # Call the cleanup function


def main():
    app.run(host="0.0.0.0")
