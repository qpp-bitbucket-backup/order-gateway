"""PDF processing utilities using PyMuPDF (fitz)."""
import fitz  # PyMuPDF
import io
from datetime import datetime, timezone
from typing import List, Optional, Union
from pathlib import Path

# Common page sizes in points (1 point = 1/72 inch)
PAGE_SIZES = {
    "a3": (841.89, 1190.55),
    "a4": (595.28, 841.89),
    "a5": (419.53, 595.28),
    "letter": (612.0, 792.0),
    "legal": (612.0, 1008.0),
    "tabloid": (792.0, 1224.0),
}

# PDF/X standards QPMN accepts on design-file upload
PDFX_STANDARDS = {
    "PDF/X-1a:2001",
    "PDF/X-1a:2003",
    "PDF/X-3:2002",
    "PDF/X-3:2003",
    "PDF/X-4:2008",
}

# Embedded sRGB ICC profile used as the OutputIntent destination profile
_SRGB_ICC_PATH = Path(__file__).resolve().parent.parent / "assets" / "srgb.icc"


def mm_to_points(mm: float) -> float:
    """Convert millimeters to PDF points."""
    return mm * 72.0 / 25.4


def parse_dimension(value: str) -> float:
    """
    Parse a dimension value. Supports:
      - Plain number (interpreted as points):  595
      - Number with 'mm' suffix:              210mm
      - Number with 'pt' suffix:              595pt
      - Number with 'in' suffix:              8.5in
      - Named size (case-insensitive):        a4
    """
    value = value.strip().lower()
    if value in PAGE_SIZES:
        return PAGE_SIZES[value][0]  # return width when used alone
    if value.endswith("mm"):
        return mm_to_points(float(value[:-2]))
    if value.endswith("pt"):
        return float(value[:-2])
    if value.endswith("in"):
        return float(value[:-2]) * 72.0
    return float(value)


class PDFProcessor:
    """Utility class for PDF processing operations."""
    
    @staticmethod
    def split_pdf(input_pdf_path: str, output_dir: str, pages_per_file: int = 1) -> List[str]:
        """
        Split a PDF file into multiple PDFs.
        
        Args:
            input_pdf_path: Path to the input PDF file
            output_dir: Directory to save split PDF files
            pages_per_file: Number of pages per split file (default: 1)
            
        Returns:
            List of paths to the split PDF files
        """
        # Open the PDF
        doc = fitz.open(input_pdf_path)
        total_pages = len(doc)
        
        if total_pages == 0:
            raise ValueError("PDF file is empty")
        
        output_files = []
        output_dir_path = Path(output_dir)
        output_dir_path.mkdir(parents=True, exist_ok=True)
        
        # Calculate number of output files
        num_files = (total_pages + pages_per_file - 1) // pages_per_file
        
        for i in range(num_files):
            start_page = i * pages_per_file
            end_page = min(start_page + pages_per_file, total_pages)
            
            # Create a new PDF document
            new_doc = fitz.open()
            
            # Insert pages into the new document
            new_doc.insert_pdf(doc, from_page=start_page, to_page=end_page - 1)
            
            # Generate output filename
            input_filename = Path(input_pdf_path).stem
            output_filename = f"{input_filename}_part_{i+1}.pdf"
            output_path = output_dir_path / output_filename
            
            # Save the new PDF
            new_doc.save(str(output_path))
            new_doc.close()
            
            output_files.append(str(output_path))
        
        doc.close()
        
        return output_files
    
    @staticmethod
    def extract_pages(input_pdf_path: str, page_numbers: List[int], output_path: str) -> str:
        """
        Extract specific pages from a PDF and save as a new PDF.
        
        Args:
            input_pdf_path: Path to the input PDF file
            page_numbers: List of page numbers to extract (1-indexed)
            output_path: Path to save the extracted PDF
            
        Returns:
            Path to the output PDF file
        """
        doc = fitz.open(input_pdf_path)
        total_pages = len(doc)
        
        # Validate page numbers
        valid_pages = [p for p in page_numbers if 1 <= p <= total_pages]
        if not valid_pages:
            raise ValueError(f"No valid pages. PDF has {total_pages} pages.")
        
        # Create new document
        new_doc = fitz.open()
        
        # Insert selected pages (convert to 0-indexed)
        for page_num in valid_pages:
            new_doc.insert_pdf(doc, from_page=page_num - 1, to_page=page_num - 1)
        
        # Save
        new_doc.save(output_path)
        new_doc.close()
        doc.close()
        
        return output_path
    
    @staticmethod
    def get_page_count(pdf_path: str) -> int:
        """
        Get the number of pages in a PDF file.
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            Number of pages
        """
        doc = fitz.open(pdf_path)
        page_count = len(doc)
        doc.close()
        return page_count
    
    @staticmethod
    def merge_pdfs(pdf_paths: List[str], output_path: str) -> str:
        """
        Merge multiple PDF files into one.
        
        Args:
            pdf_paths: List of paths to PDF files to merge
            output_path: Path to save the merged PDF
            
        Returns:
            Path to the merged PDF file
        """
        if not pdf_paths:
            raise ValueError("No PDF files to merge")
        
        merged_doc = fitz.open()
        
        for pdf_path in pdf_paths:
            doc = fitz.open(pdf_path)
            merged_doc.insert_pdf(doc)
            doc.close()
        
        merged_doc.save(output_path)
        merged_doc.close()
        
        return output_path
    
    @staticmethod
    def pdf_to_images(pdf_path: str, dpi: int = 300) -> List[bytes]:
        """
        Convert PDF pages to images.
        
        Args:
            pdf_path: Path to the PDF file
            dpi: Resolution in dots per inch (default: 300)
            
        Returns:
            List of image data as bytes (PNG format)
        """
        doc = fitz.open(pdf_path)
        images = []
        
        for page in doc:
            # Set zoom factor based on DPI
            zoom = dpi / 72
            matrix = fitz.Matrix(zoom, zoom)
            
            # Render page to image
            pix = page.get_pixmap(matrix=matrix)
            
            # Convert to PNG bytes
            img_bytes = pix.tobytes("png")
            images.append(img_bytes)
        
        doc.close()
        return images
    
    @staticmethod
    def get_pdf_info(pdf_path: str) -> dict:
        """
        Get information about a PDF file.
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            Dictionary with PDF information
        """
        doc = fitz.open(pdf_path)
        
        info = {
            "page_count": len(doc),
            "metadata": doc.metadata,
            "is_encrypted": doc.is_encrypted,
        }
        
        doc.close()
        return info

    @staticmethod
    def resize_pdf(
        input_pdf: Union[str, bytes, io.BytesIO],
        width: float,
        height: float,
        fit: bool = False,
    ) -> bytes:
        """
        Resize all pages of a PDF to the specified dimensions.

        Args:
            input_pdf: File path (str), raw bytes, or BytesIO of the input PDF.
            width:     Target page width in points.
            height:    Target page height in points.
            fit:       If True, scale content to fit the new page while
                       keeping the aspect ratio. If False, content is
                       cropped / extended to the new size.

        Returns:
            Resized PDF content as bytes.
        """
        src_doc = fitz.open(stream=input_pdf, filetype="pdf") if isinstance(input_pdf, (bytes, io.BytesIO)) else fitz.open(input_pdf)
        dst_doc = fitz.open()

        for page in src_doc:
            original_rect = page.rect
            new_page = dst_doc.new_page(width=width, height=height)

            if fit:
                # Calculate scale to fit while maintaining aspect ratio
                scale_x = width / original_rect.width
                scale_y = height / original_rect.height
                scale = min(scale_x, scale_y)
                scaled_w = original_rect.width * scale
                scaled_h = original_rect.height * scale
                offset_x = (width - scaled_w) / 2
                offset_y = (height - scaled_h) / 2
                # Target rect for the scaled content
                target_rect = fitz.Rect(offset_x, offset_y, offset_x + scaled_w, offset_y + scaled_h)
                new_page.show_pdf_page(target_rect, src_doc, page.number)
            else:
                target_rect = fitz.Rect(0, 0, width, height)
                new_page.show_pdf_page(target_rect, src_doc, page.number)

        result_bytes = dst_doc.tobytes()
        dst_doc.close()
        src_doc.close()
        return result_bytes

    @staticmethod
    def apply_pdfx(doc: "fitz.Document", standard: str = "PDF/X-4:2008") -> bool:
        """
        Declare a PDF as compliant with a PDF/X standard (ISO 15930).

        Does three things required by every PDF/X flavor:
          1. Sets ``Trapped`` to false plus a title in the Info dict
             (set_metadata also rewrites XMP, so it must come first);
          2. Writes XMP metadata with ``pdfxid:GTS_PDFXVersion`` + ``dc:title``;
          3. Adds a catalog ``/OutputIntents`` entry (``/GTS_PDFX``) with an
             embedded sRGB ICC profile as ``/DestOutputProfile``.

        Suitable for raster-only design PDFs (no text/fonts, DeviceRGB).
        PDFs with unembedded fonts or PDF/X-1a CMYK requirements need a real
        converter (Ghostscript / callas) instead.

        Args:
            doc:      Open PyMuPDF document (modified in place, before save).
            standard: One of ``PDFX_STANDARDS`` — use PDF/X-4:2008 for RGB
                      image content; X-1a forbids RGB.

        Returns:
            True if applied; False when the sRGB ICC asset is missing
            (caller should keep the original file in that case).
        """
        if standard not in PDFX_STANDARDS:
            raise ValueError(
                f"Unsupported PDF/X standard: {standard!r}. "
                f"Allowed: {sorted(PDFX_STANDARDS)}"
            )
        if not _SRGB_ICC_PATH.exists():
            return False

        title = (doc.metadata or {}).get("title") or "Design"
        now_pdf = datetime.now(timezone.utc).strftime("D:%Y%m%d%H%M%SZ")
        now_xmp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 1. Info dict — PDF/X requires a title and Trapped=false.
        #    Must run BEFORE writing the XMP: set_metadata() rewrites the
        #    document's XMP metadata stream and would wipe our custom one.
        meta = dict(doc.metadata or {})
        meta["title"] = title
        meta["producer"] = meta.get("producer") or "order-gateway"
        meta["creationDate"] = now_pdf
        meta["modDate"] = now_pdf
        meta["trapped"] = False
        doc.set_metadata(meta)
        # Ensure Trapped lands in the catalog Info even when MuPDF
        # serializes it inline (set_metadata's copy can be dropped on save)
        doc.xref_set_key(doc.pdf_catalog(), "Info/Trapped", "false")

        # 2. XMP metadata stream declaring the standard
        xmp = (
            "<?xpacket begin=\"\ufeff\" id=\"W5M0MpCehiHzreSzNTczkc9d\"?>\n"
            "<x:xmpmeta xmlns:x=\"adobe:ns:meta/\" x:xmptk=\"order-gateway\">\n"
            " <rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">\n"
            "  <rdf:Description rdf:about=\"\"\n"
            "    xmlns:dc=\"http://purl.org/dc/elements/1.1/\"\n"
            "    xmlns:xmp=\"http://ns.adobe.com/xap/1.0/\"\n"
            "    xmlns:pdfxid=\"http://www.npes.org/pdfx/ns/id/\">\n"
            "   <dc:title><rdf:Alt><rdf:li xml:lang=\"x-default\">"
            f"{title}</rdf:li></rdf:Alt></dc:title>\n"
            "   <xmp:CreatorTool>order-gateway</xmp:CreatorTool>\n"
            f"   <xmp:CreateDate>{now_xmp}</xmp:CreateDate>\n"
            f"   <xmp:ModifyDate>{now_xmp}</xmp:ModifyDate>\n"
            f"   <pdfxid:GTS_PDFXVersion>{standard}</pdfxid:GTS_PDFXVersion>\n"
            "  </rdf:Description>\n"
            " </rdf:RDF>\n"
            "</x:xmpmeta>\n"
            "<?xpacket end=\"w\"?>"
        )
        xmp_xref = doc.get_new_xref()
        doc.update_object(xmp_xref, "<< /Type /Metadata /Subtype /XML >>")
        doc.update_stream(xmp_xref, xmp.encode("utf-8"))
        doc.xref_set_key(doc.pdf_catalog(), "Metadata", f"{xmp_xref} 0 R")

        # 3. OutputIntent with embedded sRGB ICC profile
        icc_xref = doc.get_new_xref()
        doc.update_object(icc_xref, "<<>>")
        doc.update_stream(icc_xref, _SRGB_ICC_PATH.read_bytes())
        doc.xref_set_key(icc_xref, "N", "3")  # RGB profile has 3 components

        oi_xref = doc.get_new_xref()
        doc.update_object(oi_xref, "<<>>")
        doc.xref_set_key(oi_xref, "Type", "/OutputIntent")
        doc.xref_set_key(oi_xref, "S", "/GTS_PDFX")
        doc.xref_set_key(oi_xref, "OutputConditionIdentifier", "(sRGB IEC61966-2.1)")
        doc.xref_set_key(oi_xref, "Info", "(sRGB IEC61966-2.1)")
        doc.xref_set_key(oi_xref, "RegistryName", "(http://www.color.org)")
        doc.xref_set_key(oi_xref, "DestOutputProfile", f"{icc_xref} 0 R")
        doc.xref_set_key(doc.pdf_catalog(), "OutputIntents", f"[{oi_xref} 0 R]")
        return True

    @staticmethod
    def convert_to_pdfx(
        input_pdf: Union[str, bytes, io.BytesIO],
        standard: str = "PDF/X-4:2008",
    ) -> bytes:
        """
        Return ``input_pdf`` bytes declared compliant with ``standard``.

        See ``apply_pdfx`` for what is written and its limitations.
        """
        if isinstance(input_pdf, (bytes, io.BytesIO)):
            doc = fitz.open(stream=input_pdf, filetype="pdf")
        else:
            doc = fitz.open(input_pdf)
        try:
            if not pdf_processor.apply_pdfx(doc, standard):
                raise FileNotFoundError(f"sRGB ICC profile not found: {_SRGB_ICC_PATH}")
            return doc.tobytes(garbage=3, deflate=True)
        finally:
            doc.close()

    @staticmethod
    def get_page_size(pdf_path: str, page_number: int = 0) -> dict:
        """
        Get the size of a specific page in a PDF file.

        Args:
            pdf_path:    Path to the PDF file.
            page_number: 0-indexed page number (default: 0).

        Returns:
            Dict with width/height in points and mm.
        """
        doc = fitz.open(pdf_path)
        page = doc[page_number]
        rect = page.rect
        doc.close()
        return {
            "width_pt": round(rect.width, 2),
            "height_pt": round(rect.height, 2),
            "width_mm": round(rect.width * 25.4 / 72, 2),
            "height_mm": round(rect.height * 25.4 / 72, 2),
        }


# Create singleton instance
pdf_processor = PDFProcessor()
