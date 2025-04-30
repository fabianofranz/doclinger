import os
import tempfile

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    AcceleratorDevice,
    AcceleratorOptions,
    PdfPipelineOptions,
    TesseractCliOcrOptions,
)
from docling.document_converter import DocumentConverter, PdfFormatOption
from flask import Flask, request, render_template, send_from_directory, abort
from markdown import markdown as md_to_html
from pdf2image import convert_from_path
from werkzeug.utils import secure_filename

app = Flask(__name__)

upload_folder = tempfile.mkdtemp()
output_folder = os.path.join(upload_folder, "converted")
os.makedirs(output_folder, exist_ok=True)

app.config['UPLOAD_FOLDER'] = upload_folder
app.config['OUTPUT_FOLDER'] = output_folder


@app.route('/', methods=['GET'])
def upload_form():
    return render_template('upload_form.html')


ALLOWED_EXTENSIONS = {'pdf'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


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
def serve_converted_file(base_name):
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
        markdown_text = extract_markdown_from_pdf(file_path, technique)
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


def extract_markdown_from_pdf(pdf_path, technique):
    app.logger.info("Converting markdown with technique %s", technique)

    if technique == 'default':
        converter = DocumentConverter()
        result = converter.convert(pdf_path)
        markdown_text = result.document.export_to_markdown()
        return markdown_text

    elif technique == 'easyocr':
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        pipeline_options.do_table_structure = True
        pipeline_options.table_structure_options.do_cell_matching = True
        pipeline_options.ocr_options.use_gpu = False
        pipeline_options.ocr_options.lang = ["en"]
        pipeline_options.accelerator_options = AcceleratorOptions(
            num_threads=3, device=AcceleratorDevice.AUTO
        )
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
        result = converter.convert(pdf_path)
        markdown_text = result.document.export_to_markdown()
        return markdown_text

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
        pipeline_options.accelerator_options = AcceleratorOptions(
            num_threads=3, device=AcceleratorDevice.AUTO
        )
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
        result = converter.convert(image_path)
        markdown_text = result.document.export_to_markdown()
        return markdown_text

    elif technique == 'tesseract':
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        pipeline_options.do_table_structure = True
        pipeline_options.table_structure_options.do_cell_matching = True
        ocr_options = TesseractCliOcrOptions(force_full_page_ocr=True, lang=['en'])
        pipeline_options.ocr_options = ocr_options
        pipeline_options.accelerator_options = AcceleratorOptions(
            num_threads=3, device=AcceleratorDevice.AUTO
        )
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
        result = converter.convert(pdf_path)
        markdown_text = result.document.export_to_markdown()
        return markdown_text

    else:
        abort(400, description="Unsupported technique")


if __name__ == '__main__':
    app.run(debug=True)
