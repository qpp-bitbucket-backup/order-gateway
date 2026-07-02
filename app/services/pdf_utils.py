"""PDF processing utilities using PyMuPDF (fitz)."""
import fitz  # PyMuPDF
import io
from typing import List, Optional
from pathlib import Path


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


# Create singleton instance
pdf_processor = PDFProcessor()
