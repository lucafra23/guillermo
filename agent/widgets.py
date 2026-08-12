from django import forms
from unfold_markdown import MarkdownWidget

class DynamicMarkdownWidget(MarkdownWidget):
    @property
    def media(self):
        media = super().media
        js_list = []
        for js_file in media._js:
            if "unfold_markdown" in js_file and "easymde" not in js_file:
                js_list.append("js/dynamic_markdown.js")
            else:
                js_list.append(js_file)
        
        if "js/dynamic_markdown.js" not in js_list:
            js_list.append("js/dynamic_markdown.js")
            
        return forms.Media(
            css=media._css,
            js=js_list
        )