import json
import os
import tempfile
import httpx
from pathlib import Path


from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    AcceleratorDevice,
    AcceleratorOptions,
    PdfPipelineOptions,
    EasyOcrOptions,
)
from docling.document_converter import DocumentConverter, PdfFormatOption
from flask import Flask, request, render_template, send_from_directory, abort, send_file
from markdown import markdown as md_to_html
from pdf2image import convert_from_path
from werkzeug.utils import secure_filename

app = Flask(__name__)

# TODO file upload structure must be improved for a multi-tenant environment
upload_folder = tempfile.mkdtemp()
output_folder = os.path.join(upload_folder, "converted")
os.makedirs(output_folder, exist_ok=True)

app.config['UPLOAD_FOLDER'] = upload_folder
app.config['OUTPUT_FOLDER'] = output_folder

ALLOWED_EXTENSIONS = {'pdf'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/', methods=['GET'])
def upload_form():
    return render_template('upload_form.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'document' not in request.files:
        return "No file part", 400

    file = request.files['document']

    if file.filename == '':
        return "No selected file", 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

        app.logger.info("Saving to %s", file_path)
        file.save(file_path)

        # Return a successful upload response with the base name (without extension)
        base_name = filename.rsplit('.', 1)[0]
        return render_template(
            'upload_success.html',
            filename=filename,
            base_name=base_name
        )
    else:
        return "Invalid file format. Only PDF files are allowed.", 400


@app.route('/converted/<base_name>')
async def serve_converted_file(base_name):
    # Get the format parameter from the URL query string (default to png if not provided)
    file_format = request.args.get('format', 'png')
    render = request.args.get('render', 'false').lower() == 'true'
    technique = request.args.get('technique', 'default')

    # Determine the path to the original file
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{base_name}.pdf")
    if not os.path.exists(file_path):
        abort(404)

    if file_format == 'png':
        # Convert the first page of the PDF to PNG
        image_filename = f"{base_name}.png"
        image_path = os.path.join(app.config['OUTPUT_FOLDER'], image_filename)
        images = convert_from_path(file_path, fmt='png', size=(600, None))
        if images:
            images[0].save(image_path, 'PNG')
            return send_from_directory(app.config['OUTPUT_FOLDER'], image_filename)
        else:
            abort(500, description="Error converting PDF to PNG")

    elif file_format == 'markdown':
        # Convert PDF text to Markdown using PyMuPDF (or any other library)
        markdown_filename = f"{base_name}.md"
        markdown_path = os.path.join(app.config['OUTPUT_FOLDER'], markdown_filename)
        markdown_text = await extract_markdown_from_pdf(file_path, base_name, technique)
        with open(markdown_path, 'w', encoding='utf-8') as f:
            f.write(markdown_text)

        if render:
            # Convert the Markdown to HTML if the render flag is set
            html_content = md_to_html(markdown_text)
            full_html = f"""
                <!doctype html>
                <html lang="en">
                <head>
                    <meta charset="UTF-8">
                    <title>Rendered Markdown</title>
                    <style>
                        body {{
                            font-family: sans-serif;
                            padding: 1em;
                            max-width: 700px;
                            margin: auto;
                            background-color: white;
                            font-size: 75%;
                        }}
                        h1, h2, h3 {{ color: #333; }}
                        pre {{ background-color: #f4f4f4; padding: 0.5em; }}
                        code {{ background-color: #f0f0f0; padding: 2px 4px; }}
                    </style>
                </head>
                <body>
                    {html_content}
                </body>
                </html>
            """
            return full_html

        return send_from_directory(app.config['OUTPUT_FOLDER'], markdown_filename, mimetype='text/plain')

    else:
        abort(400, description="Unsupported format")


async def extract_markdown_from_pdf(pdf_path, base_name, technique):
    app.logger.info("Converting markdown with technique %s", technique)

    if technique == 'default':
        converter = DocumentConverter()
        result = converter.convert(pdf_path)
        markdown_text = result.document.export_to_markdown()
        return markdown_text

    if technique == 'default_docling_serve':
        file_path = Path(pdf_path)

        files = {
            "files": (file_path.name, file_path.open("rb"), "application/pdf"),
        }

        base_url = "http://0.0.0.0:5001/v1alpha/convert/file"
        payload = {
            "to_formats": ["md", "json"],
            "image_export_mode": "embedded",
            "do_ocr": True,
            "abort_on_error": False,
            "return_as_file": False,
        }

        response = await httpx.AsyncClient(timeout=None).post(
            base_url, files=files, data=payload
        )

        if response.status_code != 200:
            abort(500, description="Error converting with docling-serve backend")

        response_json = response.json()
        markdown_content = response_json["document"]["md_content"]
        json_content = response_json["document"]["json_content"]

        json_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{base_name}_{technique}.json")

        with open(json_path, 'w', encoding='utf-8') as f:
            print("Writing", json_path)
            json.dump(json_content, f)

        return markdown_content

    elif technique == 'easyocr':
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        pipeline_options.do_table_structure = True
        pipeline_options.table_structure_options.do_cell_matching = True
        pipeline_options.ocr_options.use_gpu = False
        pipeline_options.ocr_options.lang = ["en"]
        pipeline_options.generate_picture_images = True
        pipeline_options.do_picture_classification = True
        pipeline_options.do_formula_enrichment = True
        pipeline_options.images_scale = 1
        ocr_options = EasyOcrOptions(force_full_page_ocr=True, lang=['en'])
        pipeline_options.ocr_options = ocr_options
        pipeline_options.accelerator_options = AcceleratorOptions(
            num_threads=4, device=AcceleratorDevice.CPU
        )
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
        result = converter.convert(pdf_path)
        markdown_text = result.document.export_to_markdown()
        return markdown_text

    elif technique == 'easyocr_docling_serve':
        file_path = Path(pdf_path)

        files = {
            "files": (file_path.name, file_path.open("rb"), "application/pdf"),
        }

        base_url = "http://0.0.0.0:5001/v1alpha/convert/file"
        payload = {
            "to_formats": ["md", "json"],
            "image_export_mode": "embedded",
            "do_ocr": True,
            "force_ocr": True,
            "ocr_engine": "easyocr",
            "ocr_lang": "en",
            "abort_on_error": False,
            "return_as_file": False,
        }

        response = await httpx.AsyncClient(timeout=None).post(
            base_url, files=files, data=payload
        )

        if response.status_code != 200:
            abort(500, description="Error converting with docling-serve backend")

        markdown_content = response.json()["document"]["md_content"]
        json_content = response.json()["document"]["json_content"]

        json_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{base_name}_{technique}.json")

        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_content, f)

        return markdown_content

    elif technique == 'easyocr_from_png':
        image_path = f"{pdf_path}.png"
        images = convert_from_path(pdf_path, fmt='png')
        if images:
            images[0].save(image_path, 'PNG')
        else:
            abort(500, description="Error converting PDF to PNG")

        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        pipeline_options.do_table_structure = True
        pipeline_options.table_structure_options.do_cell_matching = True
        pipeline_options.ocr_options.use_gpu = False
        pipeline_options.ocr_options.lang = ["en"]
        pipeline_options.generate_picture_images = True
        pipeline_options.do_picture_classification = True
        pipeline_options.do_formula_enrichment = True
        pipeline_options.images_scale = 1
        ocr_options = EasyOcrOptions(force_full_page_ocr=True, lang=['en'])
        pipeline_options.ocr_options = ocr_options
        pipeline_options.accelerator_options = AcceleratorOptions(
            num_threads=4, device=AcceleratorDevice.CPU
        )
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
        result = converter.convert(image_path)
        markdown_text = result.document.export_to_markdown()
        return markdown_text

    else:
        abort(400, description="Unsupported technique")

@app.route('/docling_json/<base_name>/<technique>')
def serve_docling_json(base_name, technique):
    json_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{base_name}_{technique}.json")

    if not os.path.exists(json_path):
        abort(404)

    return send_file(json_path, mimetype='application/json')

@app.route('/editor/<base_name>/<technique>')
def docling_json_editor(base_name, technique):
    json_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{base_name}_{technique}.json")

    if not os.path.exists(json_path):
        abort(404)

    with open(json_path, 'r') as file:
        json_content = file.read()

    return render_template(
        'editor.html',
        json_path=json_path,
        json_content=json_content,
        base_name=base_name,
        technique=technique,
    )

if __name__ == '__main__':
    app.run(debug=True)
