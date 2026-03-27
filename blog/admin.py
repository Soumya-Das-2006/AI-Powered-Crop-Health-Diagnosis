from django.contrib import admin
from .models import BlogPost, Category, Comment, UserProfile

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'description')
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ('name',)

@admin.register(BlogPost)
class BlogPostAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'category', 'publish_date', 'is_published')
    list_filter = ('is_published', 'publish_date', 'category', 'author')
    search_fields = ('title', 'content')
    prepopulated_fields = {'slug': ('title',)}
    date_hierarchy = 'publish_date'
    ordering = ('-publish_date',)

@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ('author_name', 'post', 'created_date', 'is_approved')
    list_filter = ('is_approved', 'created_date')
    search_fields = ('author_name', 'content')
    actions = ['approve_comments']

    def approve_comments(self, request, queryset):
        queryset.update(is_approved=True)
    approve_comments.short_description = "Approve selected comments"

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'bio', 'website')
    search_fields = ('user__username', 'user__email', 'bio')
    list_filter = ('user__is_staff', 'user__is_active')
    readonly_fields = ('user',)
