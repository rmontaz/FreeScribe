"""
NoteStyleSelector Module

This module provides a user interface for managing note style templates and prompts.
It includes classes for selecting, creating, editing, and deleting AI prompt templates
used for generating different types of clinical notes (SOAP notes, etc.).

This software is released under the AGPL-3.0 license
Copyright (c) 2023-2024 Braedon Hendy

Further updates and packaging added in 2024 through the ClinicianFOCUS initiative, 
a collaboration with Dr. Braedon Hendy and Conestoga College Institute of Applied 
Learning and Technology as part of the CNERG+ applied research project, 
Unburdening Primary Healthcare: An Open-Source AI Clinician Partner Platform". 
Prof. Michael Yingbull (PI), Dr. Braedon Hendy (Partner), 
and Research Students - Software Developer Alex Simko, Pemba Sherpa (F24), and Naitik Patel.

Classes:
    StylePromptInfo: Data class for storing style prompt information.
    NoteStyleSelector: Main widget for selecting and managing note styles.
    StyleDialog: Dialog window for creating and editing style templates.
"""
import tkinter as tk
import UI.Helpers
from UI.Theme import Theme, Palette
from tkinter import ttk, simpledialog, messagebox
from dataclasses import dataclass
import json
import os
from utils.log_config import logger
from utils.file_utils import get_resource_path

@dataclass
class StylePromptInfo:
    """
    Data class for storing style prompt information.

    Attributes:
        style_name (str): The name of the style.
        pre_prompt (str): The prompt text that appears before the conversation.
        post_prompt (str): The prompt text that appears after the conversation.
    """
    style_name: str
    pre_prompt: str
    post_prompt: str

class NoteStyleSelector(tk.Frame):
    """
    A Tkinter frame widget for selecting and managing note styles.
    
    This class provides a dropdown selector for note styles along with
    edit and delete buttons. It manages style data persistence and
    provides static methods for accessing current style information.

    Attributes:
        current_style (str): The currently selected style name.
        style_data (dict): Dictionary containing style data with pre/post prompts.
        style_options (list): List of available style options for the dropdown.
        _styles_file (str): Path to the JSON file storing style data.
    """
    # Static class variables
    DEFAULT_STYLE = "SOAP Note - Default"
    ADD_OPTION = "Add Prompt Template..."
    NO_NOTE_STYLE = "No Note Generation"
    current_style = DEFAULT_STYLE
    # Store style data with pre/post prompts
    style_data = {}
    style_options = [ADD_OPTION, DEFAULT_STYLE]
    _styles_file = "note_styles.json"
    _styles_path = get_resource_path(_styles_file)
    
    @classmethod
    def _get_default_styles(cls):
        """
        Returns the default styles configuration.

        Returns:
            dict: Dictionary containing default style configurations with
                  pre_prompt and post_prompt for each style.
        """
        return {
            "No Note Generation": {
                "pre_prompt": "",
                "post_prompt": ""
            },
            "SOAP Note - Default": {
                "pre_prompt": "AI, please transform the following conversation into a concise SOAP note. Do not assume any medical data, vital signs, or lab values. Base the note strictly on the information provided in the conversation. Ensure that the SOAP note is structured appropriately with Subjective, Objective, Assessment, and Plan sections. Strictly extract facts from the conversation. Here's the conversation:",
                "post_prompt": "Remember, the Subjective section should reflect the patient's perspective and complaints as mentioned in the conversation. The Objective section should only include observable or measurable data from the conversation. The Assessment should be a summary of your understanding and potential diagnoses, considering the conversation's content. The Plan should outline the proposed management, strictly based on the dialogue provided. Do not add any information that did not occur and do not make assumptions. Strictly extract facts from the conversation."
            }
        }

    @classmethod
    def _get_builtin_styles(cls):
        """
        Returns every template that ships with the application.

        The default SOAP note comes first, followed by the other built-in
        templates. Built-in templates can be viewed but not edited or deleted;
        users who want a variation can add their own template.

        Returns:
            dict: Mapping of template name to its pre_prompt / post_prompt.
        """
        strict = ("Do not add any information that did not occur and do not make assumptions. "
                  "Strictly extract facts from the conversation.")
        defaults = cls._get_default_styles()
        # "No Note Generation" only records and transcribes, so it is listed after the note templates
        styles = {name: prompts for name, prompts in defaults.items() if name != cls.NO_NOTE_STYLE}
        styles.update({
            "History & Physical": {
                "pre_prompt": "AI, please transform the following conversation into a History and Physical (H&P) note. Use these headings in this order: Chief Complaint, History of Present Illness, Past Medical History, Medications, Allergies, Family History, Social History, Review of Systems, Physical Examination, Assessment, Plan. Do not assume any medical data, vital signs, or lab values. Base the note strictly on the information provided in the conversation. Here's the conversation:",
                "post_prompt": "Remember, every statement must come from the conversation. If a section was not discussed, write 'Not discussed' under that heading instead of inferring content. The Physical Examination should only include findings that were stated aloud. " + strict,
            },
            "Progress Note": {
                "pre_prompt": "AI, please transform the following conversation into a concise follow-up progress note. Use these headings in this order: Reason for Visit, Interval History, Medications, Examination, Assessment, Plan, Follow-up. Do not assume any medical data, vital signs, or lab values. Base the note strictly on the information provided in the conversation. Here's the conversation:",
                "post_prompt": "Remember, the Interval History should describe what has changed since the last visit as reported in the conversation. Leave out any heading that was not discussed rather than inventing content. " + strict,
            },
            "Referral Letter": {
                "pre_prompt": "AI, please write a referral letter to another clinician based on the following conversation. Begin with 'Dear Colleague,' and write in clear professional prose with short paragraphs covering: the reason for referral, the relevant history, examination findings, investigations and treatment so far, current medications, and the specific question or request for the receiving clinician. Do not assume any medical data, vital signs, or lab values. Base the letter strictly on the information provided in the conversation. Here's the conversation:",
                "post_prompt": "Remember, use square-bracket placeholders such as [Patient name], [Recipient] or [Referring clinician] for any detail that was not stated in the conversation, and end with 'Kind regards,' followed by a blank signature line. " + strict,
            },
            "Patient Summary (Plain Language)": {
                "pre_prompt": "AI, please write an after-visit summary for the patient based on the following conversation. Write directly to the patient ('you') in plain, friendly language that someone without medical training can understand, avoiding jargon and abbreviations. Use these headings in this order: What we talked about, What we found, Your plan, Your medicines, When to get help sooner, Follow-up. Base the summary strictly on the information provided in the conversation. Here's the conversation:",
                "post_prompt": "Remember, only include advice, medicines, tests and warning signs that were actually mentioned in the conversation, and leave out any heading that was not discussed. Do not add new medical advice. " + strict,
            },
        })
        if cls.NO_NOTE_STYLE in defaults:
            styles[cls.NO_NOTE_STYLE] = defaults[cls.NO_NOTE_STYLE]
        return styles

    @classmethod
    def is_protected(cls, style_name):
        """
        Whether a template is built in (viewable, but not editable or deletable).

        A template only counts as built in while its prompts still match the
        shipped ones, so a user's own template that happens to share a name is
        left fully under their control.

        Args:
            style_name (str): The template name to check.

        Returns:
            bool: True if the template is a built-in one.
        """
        builtin = cls._get_builtin_styles().get(style_name)
        if builtin is None:
            return False
        return cls.style_data.get(style_name, builtin) == builtin

    @classmethod
    def template_names(cls):
        """
        Returns the selectable template names (without the "Add..." entry).

        Returns:
            list: Template names in display order.
        """
        cls.ensure_loaded()
        return [name for name in cls.style_options if name != cls.ADD_OPTION]

    @classmethod
    def ensure_loaded(cls):
        """Loads the templates from disk the first time they are needed."""
        if not getattr(cls, '_styles_loaded', False):
            cls.load_styles()
            cls._styles_loaded = True

    @classmethod
    def select_style(cls, style_name):
        """
        Makes ``style_name`` the current template and remembers the choice.

        Args:
            style_name (str): The template to select.

        Returns:
            bool: True if the template exists and was selected.
        """
        cls.ensure_loaded()
        if style_name not in cls.style_data:
            return False
        cls.current_style = style_name
        cls.save_styles()
        return True

    @classmethod
    def get_prompt_info(cls, style_name):
        """
        Gets the prompt information for a named template.

        Args:
            style_name (str): The template name.

        Returns:
            StylePromptInfo: The template's prompts (empty if it does not exist).
        """
        cls.ensure_loaded()
        data = cls.style_data.get(style_name, {})
        return StylePromptInfo(
            style_name=style_name,
            pre_prompt=data.get('pre_prompt', ''),
            post_prompt=data.get('post_prompt', '')
        )

    @classmethod
    def _rebuild_options(cls):
        """Rebuilds the ordered option list: add entry, built-ins, then custom templates."""
        builtin_names = [name for name in cls._get_builtin_styles() if name in cls.style_data]
        custom = [name for name in cls.style_data if name not in builtin_names]
        cls.style_options = [cls.ADD_OPTION] + builtin_names + custom
    
    @classmethod
    def load_styles(cls):
        """
        Loads styles from disk and creates defaults if file doesn't exist.
        
        This method reads the styles JSON file, loads existing styles into
        class variables, and creates default styles if no file exists.
        Updates the style_options list with custom styles.

        Args:
            None

        Returns:
            None
        """
        try:
            if os.path.exists(cls._styles_path):
                with open(cls._styles_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    cls.style_data = data.get('styles', {})
                    cls.current_style = data.get('current_style', cls.DEFAULT_STYLE)
            else:
                # Create default styles
                cls.style_data = {}
                cls.current_style = cls.DEFAULT_STYLE

            # Make sure every built-in template is available. Templates the user
            # already has under the same name are left untouched.
            added = False
            for name, prompts in cls._get_builtin_styles().items():
                if name not in cls.style_data:
                    cls.style_data[name] = dict(prompts)
                    added = True
            if cls.current_style not in cls.style_data:
                cls.current_style = cls.DEFAULT_STYLE
            if added:
                cls.save_styles()

            cls._rebuild_options()

        except Exception as e:
            logger.exception(f"Error loading styles: {e}")
            # Fallback to defaults
            cls.style_data = {name: dict(prompts) for name, prompts in cls._get_builtin_styles().items()}
            cls.current_style = cls.DEFAULT_STYLE
            cls._rebuild_options()

    @classmethod
    def save_styles(cls):
        """
        Saves current styles to disk.
        
        Writes the current style data and selected style to the JSON file.
        Handles encoding and error management during the save process.

        Args:
            None

        Returns:
            None
        """
        try:
            data = {
                'styles': cls.style_data,
                'current_style': cls.current_style
            }
            with open(cls._styles_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.exception(f"Error saving styles: {e}")
    
    def __init__(self, root=None, parent_frame=None):
        """
        Initializes the NoteStyleSelector widget.
        
        Loads styles if not already loaded and creates the widget UI.
        Sets up the frame with the specified parent and background.

        Args:
            root (tk.Tk, optional): The root window reference.
            parent_frame (tk.Frame, optional): The parent frame to contain this widget.

        Returns:
            None
        """
        # Load styles before initializing
        NoteStyleSelector.ensure_loaded()
            
        super().__init__(parent_frame, bg=parent_frame.cget("bg"))
        self.root = root
        self.parent_frame = parent_frame
        self.create_widgets()

    def create_widgets(self):
        """
        Creates and arranges the UI widgets for the note style selector.
        
        Sets up the combobox dropdown for style selection and the
        edit/delete buttons. Binds event handlers for user interactions.

        Args:
            None

        Returns:
            None
        """
        # Frame to hold dropdown and buttons horizontally
        self.style_var = tk.StringVar(value=NoteStyleSelector.current_style)

        self.style_combo = ttk.Combobox(self, textvariable=self.style_var, 
                                       values=NoteStyleSelector.style_options, state="readonly", width=35)
        self.style_combo.pack(side=tk.LEFT, padx=(0, 5), fill=tk.X, expand=True)
        self.style_combo.bind("<<ComboboxSelected>>", self.on_style_change)

        self.edit_button = tk.Button(self, text="Edit", command=self.edit_style)
        self.edit_button.pack(side=tk.LEFT, padx=2)

        self.delete_button = tk.Button(self, text="Delete", command=self.delete_style)
        self.delete_button.pack(side=tk.LEFT, padx=2)

    @staticmethod
    def get_current_prompt_info() -> StylePromptInfo:
        """
        Gets the current style's prompt information.
        
        Returns a StylePromptInfo object containing the current style's
        name, pre-prompt, and post-prompt text.

        Args:
            None

        Returns:
            StylePromptInfo: Object containing current style information.
        """
        current_style = NoteStyleSelector.current_style
        if current_style in NoteStyleSelector.style_data:
            return StylePromptInfo(
                style_name=current_style,
                pre_prompt=NoteStyleSelector.style_data[current_style]['pre_prompt'],
                post_prompt=NoteStyleSelector.style_data[current_style]['post_prompt']
            )
        else:
            return StylePromptInfo(
                style_name=current_style,
                pre_prompt='',
                post_prompt=''
            )

    def on_style_change(self, event=None):
        """
        Handles style selection changes in the dropdown.
        
        When a user selects a different style, this method either opens
        the add style dialog (if "Add Prompt Template..." is selected)
        or updates the current style and saves the changes.

        Args:
            event (tk.Event, optional): The event object from the combobox selection.

        Returns:
            None
        """
        selected_value = self.style_var.get()
        if selected_value == "Add Prompt Template...":
            new_style_name = self.add_style()
            # Select the newly added style if it was created
            if new_style_name and new_style_name in NoteStyleSelector.style_options:
                self.style_var.set(new_style_name)
                NoteStyleSelector.current_style = new_style_name
                NoteStyleSelector.save_styles()
            elif len(NoteStyleSelector.style_options) > 1:
                # Reset to current style
                self.style_var.set(NoteStyleSelector.current_style)
        else:
            NoteStyleSelector.current_style = selected_value
            NoteStyleSelector.save_styles()

    def add_style(self):
        """
        Opens a dialog to add a new prompt template style.
        
        Creates a StyleDialog for entering new style information,
        adds the new style to the system if valid, and updates
        the dropdown and current selection.

        Args:
            None

        Returns:
            str or None: The name of the newly added style if successful, None otherwise.
        """
        # Create a custom dialog for style creation
        dialog = StyleDialog(self.root, "Add Prompt Template")
        if dialog.result:
            style_name, pre_prompt, post_prompt = dialog.result
            if style_name and style_name not in NoteStyleSelector.style_options:
                NoteStyleSelector.style_options.append(style_name)
                NoteStyleSelector.style_data[style_name] = {
                    'pre_prompt': pre_prompt,
                    'post_prompt': post_prompt
                }
                self.update_dropdown()
                # Return the new style name so it can be selected
                return style_name
        return None

    def edit_style(self):
        """
        Opens a dialog to edit or view the currently selected style.
        
        For default styles, opens in read-only view mode. For custom styles,
        allows editing of the style name and prompt content. Updates the
        system with any changes made.

        Args:
            None

        Returns:
            None
        """
        current_style = self.style_var.get()
        if current_style == "Add Prompt Template...":
            messagebox.showwarning("Edit Template", "Cannot edit 'Add Prompt Template...' option.")
            return
        
        # Get existing data if available
        existing_data = NoteStyleSelector.style_data.get(current_style, {'pre_prompt': '', 'post_prompt': ''})
        
        # Built-in templates can be viewed but not edited
        is_protected = NoteStyleSelector.is_protected(current_style)

        dialog = StyleDialog(self.root, "View Template" if is_protected else "Edit Template",
                           initial_name=current_style,
                           initial_pre=existing_data['pre_prompt'],
                           initial_post=existing_data['post_prompt'],
                           read_only=is_protected,
                           is_edit=True)
        
        if dialog.result and not is_protected:
            new_name, pre_prompt, post_prompt = dialog.result
            if new_name and new_name != current_style and new_name not in NoteStyleSelector.style_options:
                # Replace the old style with the new one
                index = NoteStyleSelector.style_options.index(current_style)
                NoteStyleSelector.style_options[index] = new_name
                
                # Remove old data and add new
                if current_style in NoteStyleSelector.style_data:
                    del NoteStyleSelector.style_data[current_style]
                NoteStyleSelector.style_data[new_name] = {
                    'pre_prompt': pre_prompt,
                    'post_prompt': post_prompt
                }
                
                self.update_dropdown()
                self.style_var.set(new_name)
                NoteStyleSelector.current_style = new_name
                NoteStyleSelector.save_styles()
            elif new_name == current_style:
                # Just update the prompts
                NoteStyleSelector.style_data[current_style] = {
                    'pre_prompt': pre_prompt,
                    'post_prompt': post_prompt
                }
                NoteStyleSelector.save_styles()

    def delete_style(self):
        """
        Deletes the currently selected style after confirmation.
        
        Prevents deletion of protected styles ("Add Prompt Template...",
        "No Note Generation", and "SOAP Note - Default"). Shows confirmation 
        dialog before deletion and resets to default style after deletion.

        Args:
            None

        Returns:
            None
        """
        current_style = self.style_var.get()
        if current_style == "Add Prompt Template..." or NoteStyleSelector.is_protected(current_style):
            messagebox.showwarning("Delete Template", "Built-in templates cannot be deleted.")
            return

        if messagebox.askyesno("Delete Template", f"Are you sure you want to delete '{current_style}'?"):
            # Check if template exists in options before attempting removal
            if current_style in NoteStyleSelector.style_options:
                NoteStyleSelector.style_options.remove(current_style)
            if current_style in NoteStyleSelector.style_data:
                del NoteStyleSelector.style_data[current_style]
            self.update_dropdown()
            # Set to default after deletion
            self.style_var.set("SOAP Note - Default")
            NoteStyleSelector.current_style = "SOAP Note - Default"
            NoteStyleSelector.save_styles()

    def update_dropdown(self):
        """
        Updates the dropdown combobox with current style options.
        
        Refreshes the values displayed in the combobox to reflect
        any changes in the available styles.

        Args:
            None

        Returns:
            None
        """
        # Update the Combobox values
        self.style_combo['values'] = NoteStyleSelector.style_options

    def apply_style(self):
        """
        Applies the currently selected style and prints its information.
        
        Retrieves the current style's prompt information and displays
        it to the console. Used for testing and debugging purposes.

        Args:
            None

        Returns:
            None
        """
        selected_style = self.style_var.get()
        if selected_style != "Add Prompt Template...":
            prompt_info = NoteStyleSelector.get_current_prompt_info()
            logger.info(f"Selected Note Style: {prompt_info.style_name}")
            logger.debug(f"Pre Prompt: {prompt_info.pre_prompt}")
            logger.debug(f"Post Prompt: {prompt_info.post_prompt}")

class StyleDialog:
    """
    A dialog window for creating and editing note style templates.
    
    This class creates a modal dialog that allows users to input or modify
    style template information including name, pre-prompt, and post-prompt.
    Supports both editable and read-only modes.

    Attributes:
        result (tuple): Contains (name, pre_prompt, post_prompt) if dialog completed successfully.
        read_only (bool): Whether the dialog is in read-only mode.
        dialog (tk.Toplevel): The dialog window.
    """
    def __init__(self, parent, title, initial_name="", initial_pre="", initial_post="", read_only=False, is_edit=False):
        """
        Initializes the StyleDialog window.
        
        Creates and displays a modal dialog for editing style information.
        Sets up the UI components and waits for user interaction.

        Args:
            parent (tk.Widget): The parent window for this dialog.
            title (str): The title to display in the dialog window.
            initial_name (str, optional): Initial value for the style name field.
            initial_pre (str, optional): Initial value for the pre-prompt field.
            initial_post (str, optional): Initial value for the post-prompt field.
            read_only (bool, optional): Whether to display in read-only mode.

        Returns:
            None
        """
        self.result = None
        self.read_only = read_only
        self.warning_frame = None  # Will hold the error warning frame
        
        # Create dialog window
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        # Slightly taller to accommodate warning
        self.dialog.geometry("900x650")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.dialog.focus_force()
        self.dialog.lift()
        
        # Make it stay on top
        self.dialog.attributes('-topmost', True)
        
        # Center the dialog
        self.dialog.geometry("+%d+%d" % (parent.winfo_rootx() + 50, parent.winfo_rooty() + 50))
             
        self.dialog.configure(bg=Palette.BG)
        heading_font = Theme.font("base", "semibold")
        side_pad = Theme.px(20)

        # Style name
        self.name_label = tk.Label(self.dialog, text="Template Name:", font=heading_font, anchor="w")
        self.name_label.pack(pady=(Theme.px(16), 4), padx=side_pad, fill=tk.X)
        self.name_entry = tk.Entry(self.dialog, width=50)
        self.name_entry.pack(pady=(0, 5), padx=side_pad, anchor="w")
        self.name_entry.insert(0, initial_name)
        if self.read_only:
            self.name_entry.configure(state='disabled')
        
        # Pre prompt section with explanation
        tk.Label(self.dialog, text="Pre Prompt:", font=heading_font, anchor="w").pack(pady=(12, 4), padx=side_pad, fill=tk.X)
        
        pre_frame = tk.Frame(self.dialog)
        pre_frame.pack(pady=5, padx=20, fill=tk.BOTH, expand=True)
        
        # Pre prompt text box
        self.pre_text = tk.Text(pre_frame, height=8, width=50, wrap="word", font=Theme.font("base"))
        self.pre_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.pre_text.insert(tk.END, initial_pre)
        if self.read_only:
            self.pre_text.configure(state='disabled')
        
        # Pre prompt explanation
        pre_explanation = (
            "This is the FIRST part of the AI prompt structure:\n\n"
            "• Acts as the opening instruction to the AI\n"
            "• Sets up how to interpret the conversation\n"
            "• Defines the document format (e.g. SOAP sections)\n"
            "• Conversation will be inserted after this\n\n"
            "Modify with caution as it affects AI output quality"
        )
        
        pre_explanation_label = tk.Label(pre_frame, text=pre_explanation, 
                                       justify=tk.LEFT, anchor='nw', 
                                       font=Theme.font("small"), fg=Palette.TEXT_MUTED,
                                       wraplength=Theme.px(215), width=35)
        pre_explanation_label.pack(side=tk.RIGHT, padx=(10, 0), fill=tk.Y)
        
        # Post prompt section with explanation
        tk.Label(self.dialog, text="Post Prompt:", font=heading_font, anchor="w").pack(pady=(12, 4), padx=side_pad, fill=tk.X)
        
        post_frame = tk.Frame(self.dialog)
        post_frame.pack(pady=5, padx=20, fill=tk.BOTH, expand=True)
        
        # Post prompt text box
        self.post_text = tk.Text(post_frame, height=8, width=50, wrap="word", font=Theme.font("base"))
        self.post_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.post_text.insert(tk.END, initial_post)
        if self.read_only:
            self.post_text.configure(state='disabled')
        
        # Post prompt explanation
        post_explanation = (
            "This is the LAST part of the AI prompt structure:\n\n"
            "• Added after the conversation text\n"
            "• Provides final formatting instructions\n"
            "• Reinforces completeness and accuracy rules\n"
            "• Helps maintain consistency\n\n"
            "Modify with caution as it affects AI output quality"
        )
        
        post_explanation_label = tk.Label(post_frame, text=post_explanation, 
                                        justify=tk.LEFT, anchor='nw', 
                                        font=Theme.font("small"), fg=Palette.TEXT_MUTED,
                                        wraplength=Theme.px(215), width=35)
        post_explanation_label.pack(side=tk.RIGHT, padx=(10, 0), fill=tk.Y)
        
        # Buttons
        button_frame = tk.Frame(self.dialog)
        button_frame.pack(pady=(14, 16), padx=side_pad, fill=tk.X)
        
        # Buttons sit at the right, primary action last
        inner_frame = tk.Frame(button_frame)
        inner_frame.pack(side=tk.RIGHT)
        
        # A read-only template can only be closed
        if self.read_only:
            ttk.Button(inner_frame, text="Close", command=self.cancel_clicked, 
                     width=10).pack(side=tk.LEFT)
        else:
            ttk.Button(inner_frame, text="Cancel", command=self.cancel_clicked, 
                     width=10).pack(side=tk.LEFT, padx=(0, 8))
            ttk.Button(inner_frame, text="OK", command=lambda: self.ok_clicked(is_edit=is_edit), 
                     width=10, style="Accent.TButton").pack(side=tk.LEFT)
        
        # Focus on name entry (if not disabled)
        if not self.read_only:
            self.name_entry.focus_set()
            self.name_entry.select_range(0, tk.END)
        
        UI.Helpers.center_window_to_parent(self.dialog, parent)
        UI.Helpers.set_window_icon(self.dialog)

        # Add warning for read-only mode
        if self.read_only:
            self.show_error_warning("Built-in templates are view-only. Add your own template to customise the wording.")

        # Wait for dialog to close
        self.dialog.wait_window()
    
    def show_error_warning(self, message):
        """
        Shows an error warning frame at the top of the dialog.
        
        Args:
            message (str): The error message to display.
        """
        # Remove existing warning frame if present
        if self.warning_frame:
            self.warning_frame.destroy()
        
        # Create new warning frame
        self.warning_frame = tk.Frame(self.dialog, bg=Palette.DANGER_SOFT)
        self.warning_frame.pack(fill=tk.X, before=self.name_label)
        warning_label = tk.Label(self.warning_frame, 
                               text=message,
                               bg=Palette.DANGER_SOFT, fg=Palette.DANGER, font=Theme.font("base", "medium"),
                               wraplength=Theme.px(820), justify=tk.LEFT)
        warning_label.pack(pady=8, padx=Theme.px(20), anchor="w")
    
    def hide_error_warning(self):
        """
        Hides the error warning frame if it exists.
        """
        if self.warning_frame:
            self.warning_frame.destroy()
            self.warning_frame = None
    
    def ok_clicked(self, is_edit=False):
        """
        Handles the OK button click event.
        
        Validates the input fields and stores the result if valid.
        Closes the dialog window after processing.

        Args:
            None

        Returns:
            None
        """
        if self.read_only:
            # Shouldn't be called in read-only mode, but just in case
            return  
            
        name = self.name_entry.get().strip()
        pre_prompt = self.pre_text.get("1.0", tk.END).strip()
        post_prompt = self.post_text.get("1.0", tk.END).strip()
        
        if not name:
            self.show_error_warning("Template name cannot be empty - Please enter a title")
            self.name_entry.focus_set()
            return
        else:
            if not is_edit and name in NoteStyleSelector.style_options:
                self.show_error_warning(f"Template '{name}' already exists - Please choose a different name")
                return
    
        # Hide any existing warning if validation passes
        self.hide_error_warning()
        
        if name:
            self.result = (name, pre_prompt, post_prompt)
        
        self.dialog.destroy()
    
    def cancel_clicked(self):
        """
        Handles the Cancel/Close button click event.
        
        Closes the dialog window without saving any changes.

        Args:
            None

        Returns:
            None
        """
        self.dialog.destroy()

if __name__ == "__main__":
    # build a sample tkinter window to test the NoteStyleSelector

    root = tk.Tk()
    root.title("Note Template Selector Test")

    note_style_selector = NoteStyleSelector(root)
    note_style_selector.pack()

    root.mainloop()
