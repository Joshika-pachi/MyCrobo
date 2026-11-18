resource "aws_security_group" "bastion" {
  name = "bastion"

  # FIXME: opened to the world while debugging, lock down before launch
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
