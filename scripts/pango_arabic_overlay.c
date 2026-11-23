#include <cairo/cairo.h>
#include <glib.h>
#include <pango/pangocairo.h>

#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    int start_frame;
    int end_frame;
    int fade_in_frames;
    int fade_out_frames;
    int center_x;
    int center_y;
    int box_width;
    double red;
    double green;
    double blue;
    char *font;
    char *text;
    int ltr; /* optional 11th field "ltr": left-to-right paragraph (English); default right-to-left */
} Cue;

static void fail(const char *message) {
    fprintf(stderr, "%s\n", message);
    exit(1);
}

static int read_cues(const char *path, Cue **out_cues) {
    FILE *file = fopen(path, "rb");
    if (!file) {
        fprintf(stderr, "Failed to open spec %s: %s\n", path, strerror(errno));
        exit(1);
    }

    Cue *cues = NULL;
    int count = 0;
    int capacity = 0;
    char line[65536];
    while (fgets(line, sizeof(line), file)) {
        if (line[0] == '\0' || line[0] == '#') {
            continue;
        }
        char *newline = strchr(line, '\n');
        if (newline) {
            *newline = '\0';
        }

        char *fields[11] = {0};
        char *cursor = line;
        int field_count = 0;
        for (int i = 0; i < 11 && cursor; i++) {
            fields[i] = cursor;
            field_count++;
            char *tab = strchr(cursor, '\t');
            if (tab) {
                *tab = '\0';
                cursor = tab + 1;
            } else {
                cursor = NULL;
            }
        }
        if (field_count < 10) {
            fail("Invalid cue spec line");
        }

        gsize font_len = 0;
        guchar *decoded_font = g_base64_decode(fields[8], &font_len);
        char *font = g_malloc(font_len + 1);
        memcpy(font, decoded_font, font_len);
        font[font_len] = '\0';
        g_free(decoded_font);

        gsize text_len = 0;
        guchar *decoded = g_base64_decode(fields[9], &text_len);
        char *text = g_malloc(text_len + 1);
        memcpy(text, decoded, text_len);
        text[text_len] = '\0';
        g_free(decoded);

        unsigned int rgb = 0;
        if (sscanf(fields[7], "%06x", &rgb) != 1) {
            fail("Invalid cue color");
        }

        if (count == capacity) {
            capacity = capacity ? capacity * 2 : 16;
            cues = realloc(cues, sizeof(Cue) * capacity);
            if (!cues) {
                fail("Out of memory");
            }
        }
        cues[count].start_frame = atoi(fields[0]);
        cues[count].end_frame = atoi(fields[1]);
        cues[count].fade_in_frames = atoi(fields[2]);
        cues[count].fade_out_frames = atoi(fields[3]);
        cues[count].center_x = atoi(fields[4]);
        cues[count].center_y = atoi(fields[5]);
        cues[count].box_width = atoi(fields[6]);
        cues[count].red = ((rgb >> 16) & 0xff) / 255.0;
        cues[count].green = ((rgb >> 8) & 0xff) / 255.0;
        cues[count].blue = (rgb & 0xff) / 255.0;
        cues[count].font = font;
        cues[count].text = text;
        cues[count].ltr = field_count >= 11 && strcmp(fields[10], "ltr") == 0;
        count++;
    }
    fclose(file);
    *out_cues = cues;
    return count;
}

static double cue_alpha(const Cue *cue, int frame) {
    if (frame < cue->start_frame || frame >= cue->end_frame) {
        return 0.0;
    }
    double alpha = 1.0;
    if (cue->fade_in_frames > 0) {
        int fade_in_pos = frame - cue->start_frame;
        if (fade_in_pos < cue->fade_in_frames) {
            alpha = fmin(alpha, (double)fade_in_pos / (double)cue->fade_in_frames);
        }
    }
    if (cue->fade_out_frames > 0) {
        int fade_out_pos = cue->end_frame - frame;
        if (fade_out_pos <= cue->fade_out_frames) {
            alpha = fmin(alpha, (double)fade_out_pos / (double)cue->fade_out_frames);
        }
    }
    return alpha;
}

int main(int argc, char **argv) {
    if (argc < 6 || argc > 8) {
        fprintf(stderr, "Usage: %s SPEC WIDTH HEIGHT FRAMES LINE_SPACING [START_FRAME [BACKGROUND_PNG]]\n", argv[0]);
        return 1;
    }

    const char *spec_path = argv[1];
    int width = atoi(argv[2]);
    int height = atoi(argv[3]);
    int frames = atoi(argv[4]);
    double line_spacing = atof(argv[5]);
    /* Optional: draw only frames START_FRAME .. START_FRAME+FRAMES-1 (one piece of the video), and paint the
       text onto BACKGROUND_PNG (already WIDTH x HEIGHT) so the frames are finished pictures, not an overlay. */
    int first_frame = argc > 6 ? atoi(argv[6]) : 0;
    cairo_surface_t *background = NULL;
    if (argc > 7) {
        background = cairo_image_surface_create_from_png(argv[7]);
        if (cairo_surface_status(background) != CAIRO_STATUS_SUCCESS) {
            fail("Cannot read the background PNG");
        }
        if (cairo_image_surface_get_width(background) != width || cairo_image_surface_get_height(background) != height) {
            fail("Background PNG has the wrong size");
        }
    }
    if (first_frame < 0) {
        fail("Invalid start frame");
    }

    if (width <= 0 || height <= 0 || frames <= 0) {
        fail("Invalid dimensions or frame count");
    }

    Cue *cues = NULL;
    int cue_count = read_cues(spec_path, &cues);

    cairo_surface_t *surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, width, height);
    cairo_t *cr = cairo_create(surface);
    PangoLayout *layout = pango_cairo_create_layout(cr);
    pango_layout_set_alignment(layout, PANGO_ALIGN_CENTER);
    pango_layout_set_wrap(layout, PANGO_WRAP_WORD_CHAR);
    pango_layout_set_auto_dir(layout, FALSE);
    pango_layout_set_line_spacing(layout, line_spacing);
    pango_context_set_base_dir(pango_layout_get_context(layout), PANGO_DIRECTION_RTL);

    int stride = cairo_image_surface_get_stride(surface);
    unsigned char *data = cairo_image_surface_get_data(surface);
    unsigned char *row_buffer = malloc((size_t)width * 4);
    if (!row_buffer) {
        fail("Out of memory");
    }

    double *shown = calloc((size_t)(cue_count > 0 ? cue_count : 1), sizeof(double)); /* alpha of each cue in the last drawn frame */
    if (!shown) {
        fail("Out of memory");
    }
    int have_picture = 0;

    for (int index = 0; index < frames; index++) {
        int frame = first_frame + index;
        /* Most frames are the same still screen as the one before: draw only when something changed. */
        int changed = !have_picture;
        for (int i = 0; i < cue_count; i++) {
            double alpha = cue_alpha(&cues[i], frame);
            if (alpha != shown[i]) {
                changed = 1;
                shown[i] = alpha;
            }
        }
        if (!changed) {
            goto write_frame;
        }
        have_picture = 1;
        cairo_save(cr);
        if (background) {
            cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
            cairo_set_source_surface(cr, background, 0, 0);
        } else {
            cairo_set_operator(cr, CAIRO_OPERATOR_CLEAR);
        }
        cairo_paint(cr);
        cairo_restore(cr);

        for (int i = 0; i < cue_count; i++) {
            double alpha = shown[i];
            if (alpha <= 0.0) {
                continue;
            }
            PangoFontDescription *font_desc = pango_font_description_from_string(cues[i].font);
            pango_layout_set_font_description(layout, font_desc);
            pango_font_description_free(font_desc);
            pango_context_set_base_dir(pango_layout_get_context(layout), cues[i].ltr ? PANGO_DIRECTION_LTR : PANGO_DIRECTION_RTL);
            pango_layout_context_changed(layout);
            pango_layout_set_width(layout, cues[i].box_width * PANGO_SCALE);
            pango_layout_set_text(layout, cues[i].text, -1);
            int text_width = 0;
            int text_height = 0;
            pango_layout_get_pixel_size(layout, &text_width, &text_height);
            int x = cues[i].center_x - cues[i].box_width / 2;
            int y = cues[i].center_y - text_height / 2;
            cairo_move_to(cr, x, y);
            cairo_set_source_rgba(cr, cues[i].red, cues[i].green, cues[i].blue, alpha);
            pango_cairo_show_layout(cr, layout);
        }

        cairo_surface_flush(surface);
write_frame:
        if (stride == width * 4) {
            fwrite(data, 1, (size_t)height * stride, stdout);
        } else {
            for (int y = 0; y < height; y++) {
                memcpy(row_buffer, data + (size_t)y * stride, (size_t)width * 4);
                fwrite(row_buffer, 1, (size_t)width * 4, stdout);
            }
        }
        if (ferror(stdout)) {
            fail("Failed to write raw frame output");
        }
    }

    free(row_buffer);
    free(shown);
    if (background) {
        cairo_surface_destroy(background);
    }
    g_object_unref(layout);
    cairo_destroy(cr);
    cairo_surface_destroy(surface);
    for (int i = 0; i < cue_count; i++) {
        g_free(cues[i].font);
        g_free(cues[i].text);
    }
    free(cues);
    return 0;
}
